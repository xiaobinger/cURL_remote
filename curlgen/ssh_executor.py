"""请求执行器：本地执行 + GateShell 堡垒机远程执行（流式输出、超时、可停止）

关于堡垒机
----------
目标跳板机是 **GateShell 菜单式堡垒机**（协议标识 `SSH-2.0-SSHD_*`），它与普通
OpenSSH 跳板机有三处根本差异（已实测确认）：

1. **不支持 SSH exec**：任何 ``exec`` 请求都会被立即关闭通道，stdout/stderr 全空，
   ``exit_status`` 返回 ``-1``。因此「堡垒机 shell 里 ssh 免密跳转」这条路走不通。
2. **不做原始 TCP 透传**：``direct-tcpip`` 通道虽然能打开，但握手回来的对端 host key
   指纹与堡垒机自身完全一致（连不同网段的机器都是同一个指纹），说明堡垒机是
   **接管** SSH 握手而非**转发**。因此「嵌套 Transport 隧道直连目标机」也走不通。
3. **唯一入口是交互式菜单**：`invoke_shell()` 会得到一个列有全部授权机器的 TUI，
   支持 ``/{ip}`` 搜索、``:{序号}`` 跳转、回车进入目标机。

所以接入路径固定为：菜单 → 搜索目标机 → 回车进入目标机 shell → 执行命令。

由于目标机侧是交互式 shell（没有退出码通道），命令执行采用哨兵标记判定完成：
``{cmd} 2>&1; echo <SENTINEL>$?``，并把 stderr 合并进 stdout。
"""
import logging
import re
import socket
import subprocess
import threading
import time
import uuid

logger = logging.getLogger("curlgen.executor")


class ExecutionError(Exception):
    """执行失败（信息已面向用户）"""


class ExecutionTimeout(ExecutionError):
    """执行超时"""


class ExecutorStop(ExecutionError):
    """用户手动停止"""


# ──────────────────────────── ANSI 处理 ────────────────────────────
_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_ANSI_CHARSET = re.compile(r"\x1b[()][A-Za-z0-9]")
_ANSI_SINGLE = re.compile(r"\x1b[=>78MDEHc]")

# GateShell 菜单行： 001: 名称(备注)   10.0.0.1:22   ssh   root
# 名称中可能含数字（tomcat01），故用 IP:port 作为右锚点做非贪婪匹配。
_GATE_ROW_RE = re.compile(
    r"(\d{3}):\s+(.*?)\s+((?:\d{1,3}\.){3}\d{1,3}):(\d+)\s+(\S+)\s+(\S+)"
)
# 菜单状态行： 37/90, name-asc
_GATE_STATUS_RE = re.compile(r"(\d+)/(\d+),\s*(\S+)")
# 目标机 shell 提示符： [root@host ~]#
_SHELL_PROMPT_RE = re.compile(r"\[[^\]\n]*\][#$][ \t]*$")


def strip_ansi(text):
    """剥离终端控制序列，还原为纯文本"""
    if not text:
        return ""
    text = _ANSI_OSC.sub("", text)
    text = _ANSI_CSI.sub("", text)
    text = _ANSI_CHARSET.sub("", text)
    text = _ANSI_SINGLE.sub("", text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_gate_hosts(text):
    """从菜单文本解析授权机器列表

    返回 [{'index','name','ip','port','proto','user'}, ...]，按菜单序号升序。
    GateShell 全屏无换行（用光标定位重绘），因此对整段文本做 findall。
    """
    hosts = {}
    for m in _GATE_ROW_RE.finditer(text):
        idx, name, ip, port, proto, user = m.groups()
        hosts[idx] = {
            "index": idx,
            "name": name.strip(),
            "ip": ip,
            "port": int(port),
            "proto": proto,
            "user": user,
        }
    return [hosts[k] for k in sorted(hosts)]


# ──────────────────────────── GateShell 会话 ────────────────────────────
class GateShellSession:
    """GateShell 菜单式堡垒机会话驱动。

    生命周期：open() → (list_hosts / connect_host) → run() → close()
    """

    CONNECT_TIMEOUT = 10     # TCP/握手/认证
    MENU_TIMEOUT = 25        # 等待菜单渲染
    TYPE_DELAY = 0.1         # 菜单输入节奏（一次性灌会被吞，必须逐字符）
    TARGET_TIMEOUT = 40      # 进入目标机 shell
    PAGE_DOWN = b"\x1b[6~"   # PageDown，用于翻页枚举

    def __init__(self, host, port, user, password, on_log=None, stop_event=None):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.on_log = on_log
        self.stop_event = stop_event
        self._transport = None
        self._chan = None
        self._in_target = False
        self._boot_text = ""   # open() 阶段读到的菜单文本，供 list_hosts() 复用

    # ── 底层 ──
    def _log(self, text):
        """状态类消息（以 [info] 形式展示）"""
        logger.info("[gate] %s", text)
        if self.on_log:
            try:
                self.on_log("info", text)
            except Exception as e:
                logger.warning("GateShell 回调异常: %s", e)

    def _check_stop(self):
        if self.stop_event is not None and self.stop_event.is_set():
            raise ExecutorStop("已手动停止")

    def open(self):
        """连接堡垒机并进入菜单"""
        import paramiko

        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.CONNECT_TIMEOUT)
        except socket.timeout:
            raise ExecutionError(f"连接堡垒机超时（{self.CONNECT_TIMEOUT}s，请检查网络/地址）")
        except Exception as e:
            raise ExecutionError(f"连接堡垒机失败: {e}")

        try:
            sock.settimeout(self.CONNECT_TIMEOUT)
            self._transport = paramiko.Transport(sock)
            self._transport.start_client(timeout=self.CONNECT_TIMEOUT)
            self._transport.auth_password(self.user, self.password)
        except paramiko.ssh_exception.AuthenticationException as e:
            self.close()
            raise ExecutionError(f"堡垒机认证失败（用户名或密码错误）: {e}")
        except socket.timeout:
            self.close()
            raise ExecutionError(f"连接堡垒机超时（{self.CONNECT_TIMEOUT}s）")
        except Exception as e:
            self.close()
            raise ExecutionError(f"连接堡垒机失败: {e}")

        self._log(f"堡垒机已连接 {self.user}@{self.host}:{self.port}")

        try:
            self._chan = self._transport.open_session(timeout=self.CONNECT_TIMEOUT)
            self._chan.settimeout(1.0)
            self._chan.get_pty(term="xterm", width=200, height=50)
            self._chan.invoke_shell()
        except Exception as e:
            self.close()
            raise ExecutionError(f"打开堡垒机会话失败（该堡垒机可能不支持交互式菜单）: {e}")

        # 等待菜单渲染完成
        raw, _ = self._pump(idle=4.0,
                            hard_deadline=time.monotonic() + self.MENU_TIMEOUT)
        text = strip_ansi(raw.decode("utf-8", "replace"))
        # 这段菜单数据后续 list_hosts() 还要用，先缓存（否则会被后续读取挤掉）
        self._boot_text = text
        if "Quit: Use" not in text and not _GATE_ROW_RE.search(text):
            self.close()
            raise ExecutionError("堡垒机未返回授权机器菜单（可能不是 GateShell 类型堡垒机）")
        self._log("已进入授权机器菜单")

    def _pump(self, idle=4.0, until=None, hard_deadline=None):
        """读取通道数据。

        until   : 正则，在「已剥离 ANSI 且 \\r 已归一为 \\n」的文本上匹配，
                  命中立即返回 (raw, match)
        idle    : 静默多少秒后认为读完（收到数据会重置）
        返回 (原始 bytes, match 或 None)
        """
        raw = b""
        last = time.monotonic()
        while True:
            self._check_stop()
            now = time.monotonic()
            if hard_deadline and now > hard_deadline:
                break
            if now - last > idle:
                break
            if self._chan is None or self._chan.closed:
                break
            try:
                if self._chan.recv_ready():
                    raw += self._chan.recv(65536)
                    last = time.monotonic()
                    if until is not None:
                        # 在归一化文本上匹配：哨兵需按「独占一行」锚定
                        m = until.search(strip_ansi(raw.decode("utf-8", "replace")))
                        if m:
                            return raw, m
                else:
                    time.sleep(0.05)
            except socket.timeout:
                continue
            except (EOFError, OSError) as e:
                logger.debug("读取通道结束: %s", e)
                break
        m = (until.search(strip_ansi(raw.decode("utf-8", "replace")))
             if until else None)
        return raw, m

    def _drain(self, seconds=1.2):
        """丢弃通道中的残留输出（欢迎语 / 提示符 / 探测回显）"""
        self._pump(idle=0.4, hard_deadline=time.monotonic() + seconds)

    def _type(self, text, delay=None):
        """逐字符输入。菜单输入必须逐字符（一次性发送会被吞）。"""
        if self._chan is None or self._chan.closed:
            raise ExecutionError("堡垒机会话已断开")
        if delay is None:
            delay = self.TYPE_DELAY
        for ch in text:
            self._check_stop()
            self._chan.send(ch.encode("utf-8"))
            if delay:
                time.sleep(delay)

    # ── 授权机器枚举 ──
    def list_hosts(self, max_pages=12):
        """翻页收集全部授权机器"""
        boot = self._boot_text
        if not boot:
            raw, _ = self._pump(idle=4.0,
                                hard_deadline=time.monotonic() + self.MENU_TIMEOUT)
            boot = strip_ansi(raw.decode("utf-8", "replace"))
            self._boot_text = boot
        hosts = {h["index"]: h for h in parse_gate_hosts(boot)}
        total = None
        m = _GATE_STATUS_RE.search(boot)
        if m:
            total = int(m.group(2))

        # 翻页补全（菜单用光标定位重绘，需逐页累积）
        for _ in range(max_pages):
            if total and len(hosts) >= total:
                break
            self._check_stop()
            self._chan.send(self.PAGE_DOWN)
            chunk, _ = self._pump(idle=3.0,
                                  hard_deadline=time.monotonic() + self.TARGET_TIMEOUT)
            text = strip_ansi(chunk.decode("utf-8", "replace"))
            before = len(hosts)
            for h in parse_gate_hosts(text):
                hosts[h["index"]] = h
            if len(hosts) == before:
                break  # 没有新行，可能已到末页

        result = [hosts[k] for k in sorted(hosts)]
        self._log(f"已加载 {len(result)} 台授权机器" + (f" / 共 {total}" if total else ""))
        return result

    def find_host(self, target):
        """在授权列表中按 IP（或名称）查找，未找到返回 None。

        先查首屏再翻页：多数目标机排在靠前位置，可省掉数秒翻页开销。
        """
        for h in parse_gate_hosts(self._boot_text):
            if h["ip"] == target or h["name"] == target:
                return h
        for h in self.list_hosts():
            if h["ip"] == target or h["name"] == target:
                return h
        return None

    def total_hosts(self):
        """菜单状态行给出的授权机器总数（不翻页，-1 表示未取到）"""
        m = _GATE_STATUS_RE.search(self._boot_text or "")
        return int(m.group(2)) if m else -1

    # ── 进入目标机 ──
    def connect_host(self, target):
        """经菜单搜索并进入目标机 shell"""
        if self._in_target:
            return
        self._log(f"在菜单中搜索 {target} ...")
        # 菜单输入必须逐字符
        self._type(f"/{target}")
        time.sleep(1.2)
        self._chan.send(b"\r")           # 执行搜索
        self._pump(idle=2.5, hard_deadline=time.monotonic() + 10)
        self._chan.send(b"\r")           # 回车进入选中行

        # 用一次哨兵回显确认目标机 shell 已经可交互（比匹配提示符更可靠）。
        # 哨兵必须「独占一行」，否则会命中终端回显的那行命令。
        ready = f"__CG_READY_{uuid.uuid4().hex[:8]}__"
        deadline = time.monotonic() + self.TARGET_TIMEOUT
        time.sleep(1.0)
        self._type(f"echo {ready}", delay=0.02)
        self._chan.send(b"\r")
        raw, m = self._pump(
            until=re.compile(r"(?m)^" + re.escape(ready) + r"[ \t]*$"),
            idle=5.0, hard_deadline=deadline)
        if not m:
            text = strip_ansi(raw.decode("utf-8", "replace")).strip()
            hint = ""
            low = text.lower()
            if "permission" in low or "denied" in low or "not authorized" in low:
                hint = "（堡垒机拒绝了该连接）"
            elif "not found" in low or "no such" in low:
                hint = "（菜单中未找到该机器）"
            self.close()
            raise ExecutionError(
                f"进入目标机 {target} 失败{hint}，请确认该机器在堡垒机授权列表内。{_tail(text)}"
            )
        self._in_target = True
        # 排空欢迎语/提示符/哨兵回显，否则会污染第一条业务命令的输出
        self._drain()
        self._log(f"已进入目标机 {target}")

    # ── 执行命令 ──
    def run(self, command, timeout=30):
        """在目标机执行命令，返回 (exit_code, 合并后的输出)"""
        if not self._in_target:
            raise ExecutionError("尚未进入目标机，无法执行命令")

        token = uuid.uuid4().hex[:10]
        start_mark = f"__CG_S_{token}__"
        end_mark = f"__CG_E_{token}__"
        # START/END 双哨兵，两者都必须「独占一行」才算数，否则会误命中终端回显的命令行。
        # 先捕获退出码再补一个换行：否则当命令输出不带结尾换行时（如 JSON 响应），
        # END 标记会粘在正文末尾，匹配不到。
        # stderr 合并进 stdout（交互式 shell 两者都在 pty 上，无法分开）。
        wrapped = (f"echo {start_mark}; {command} 2>&1; "
                   f"__ec=$?; echo; echo {end_mark}$__ec")
        deadline = time.monotonic() + timeout + 10
        # 匹配在「已剥离 ANSI、\r 已归一」的文本上进行
        start_pat = re.compile(r"(?m)^" + re.escape(start_mark) + r"[ \t]*$")
        end_pat = re.compile(r"(?m)^" + re.escape(end_mark) + r"(\d+)[ \t]*$")

        self._log(f"执行: {command}")
        self._type(wrapped, delay=0)
        self._chan.send(b"\r")

        # 关键：始终在「累计缓冲区」上匹配。
        # 阶段一命中 START 立刻返回时，同一批数据里往往已含 END
        # （echo 这类快命令整段输出会合并成一个 TCP 包），
        # 若只在「新读到」的数据里找 END 就会永远匹配不上。
        raw = b""

        def matched(pattern):
            return pattern.search(strip_ansi(raw.decode("utf-8", "replace")))

        # ── 阶段一：等 START 独占成行，说明命令真正开始执行 ──
        # until 命中立即返回，避免空等 idle 超时才进入流式阶段
        while time.monotonic() < deadline and not matched(start_pat):
            chunk, _ = self._pump(until=start_pat, idle=5.0, hard_deadline=deadline)
            if chunk:
                raw += chunk
        if not matched(start_pat):
            self.close()
            raise ExecutionTimeout(f"目标机未开始执行命令（>{timeout}s），已断开")

        # ── 阶段二：流式读取到 END 行 ──
        emitted = 0
        match = None
        while time.monotonic() < deadline:
            match = matched(end_pat)
            if match:
                break
            chunk, _ = self._pump(idle=3.0, hard_deadline=deadline)
            if not chunk:
                continue
            raw += chunk
            body = _extract_body(raw, start_mark, end_mark)
            if len(body) > emitted:
                delta = body[emitted:]
                emitted = len(body)
                if delta.strip():
                    # on_log 回调约定：tag/text 两个参数
                    if self.on_log:
                        self.on_log("stdout", delta)

        if not match:
            self.close()
            raise ExecutionTimeout(f"请求超时（>{timeout}s），已断开堡垒机会话")

        exit_code = int(match.group(1))
        raw_body = _extract_body(raw, start_mark, end_mark)
        # 快命令的完整输出往往在第一次读取时就已整段到达，阶段二一进来就命中 END
        # 直接返回，上面的流式回调一次都不会触发；这里补发未推送的正文，
        # 保证响应报文一定会被展示（UI 的 streamed 模式依赖流式回调出正文）。
        if self.on_log and len(raw_body) > emitted and raw_body[emitted:].strip():
            self.on_log("stdout", raw_body[emitted:])
        body = raw_body.strip("\r\n")
        # 去掉尾部残留的 shell 提示符
        body = _SHELL_PROMPT_RE.sub("", body.rstrip()).rstrip()
        self._log(f"执行结束 exit_code={exit_code}")
        return exit_code, body

    def close(self):
        for obj in (self._chan, self._transport):
            if obj is not None:
                try:
                    obj.close()
                except Exception as e:
                    logger.debug("关闭 GateShell 对象失败: %s", e)
        self._chan = None
        self._transport = None
        self._in_target = False


def _extract_body(raw, start_mark, end_mark):
    """从原始数据里剥出真实命令输出。

    以「独占一行的 START」为左界、「独占一行的 END」为右界，
    这样既不会被终端回显的命令行干扰，也不受 TUI 折行影响。
    """
    text = strip_ansi(raw.decode("utf-8", "replace"))
    m = re.search(r"(?m)^" + re.escape(start_mark) + r"[ \t]*$", text)
    if m:
        text = text[m.end():]
    e = re.search(r"(?m)^" + re.escape(end_mark) + r"\d+[ \t]*$", text)
    if e:
        text = text[:e.start()]
    return text


def _tail(text, n=160):
    text = " ".join(text.split())
    return f"\n远端输出：{text[-n:]}" if text else ""


# ──────────────────────────── 本地执行 ────────────────────────────
def _read_stream(stream, tag, parts, on_progress):
    """读取流直到 EOF，逐行回调（在独立线程中运行）"""
    try:
        for raw in iter(stream.readline, b""):
            if not raw:
                break
            try:
                line = raw.decode("utf-8", errors="replace")
            except Exception:
                line = str(raw)
            parts.append(line)
            if on_progress:
                on_progress(tag, line)
    except Exception as e:
        logger.warning("读取 %s 流异常: %s", tag, e)


class LocalExecutor:
    """本地执行：Popen + 双线程实时读取 stdout/stderr"""

    def __init__(self, on_progress=None):
        self.on_progress = on_progress
        self._proc = None
        self._stop_flag = threading.Event()

    def stop(self):
        self._stop_flag.set()
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
                logger.info("已终止本地进程 pid=%s", proc.pid)
            except Exception as e:
                logger.warning("终止本地进程失败: %s", e)

    def run(self, command, timeout=30):
        logger.info("本地执行开始, timeout=%ss", timeout)
        logger.debug("本地执行命令: %s", command)
        self._stop_flag.clear()
        try:
            self._proc = subprocess.Popen(
                command, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except Exception as e:
            logger.error("本地启动进程失败: %s", e)
            raise ExecutionError(f"本地启动进程失败: {e}")

        stdout_parts, stderr_parts = [], []
        t1 = threading.Thread(target=_read_stream, args=(self._proc.stdout, "stdout", stdout_parts, self.on_progress), daemon=True)
        t2 = threading.Thread(target=_read_stream, args=(self._proc.stderr, "stderr", stderr_parts, self.on_progress), daemon=True)
        t1.start()
        t2.start()

        deadline = time.monotonic() + timeout
        while self._proc.poll() is None:
            if self._stop_flag.is_set():
                self._proc.kill()
                t1.join(2)
                t2.join(2)
                raise ExecutorStop("已手动停止")
            if time.monotonic() > deadline:
                self._proc.kill()
                t1.join(2)
                t2.join(2)
                raise ExecutionTimeout(f"请求超时 ({timeout}s)")
            time.sleep(0.05)

        t1.join(5)
        t2.join(5)
        exit_code = self._proc.returncode
        stdout = "".join(stdout_parts)
        stderr = "".join(stderr_parts)
        logger.info("本地执行结束 exit_code=%s stdout=%dB stderr=%dB", exit_code, len(stdout), len(stderr))
        return {"exit_code": exit_code, "stdout": stdout, "stderr": stderr}


# ──────────────────────────── 堡垒机远程执行 ────────────────────────────
def _port(value, default=22):
    v = (value or "").strip()
    try:
        return int(v) if v else default
    except ValueError:
        return default


class SSHRemoteExecutor:
    """通过 GateShell 堡垒机在目标机上执行命令。

    接入路径（实测）：密码认证 → invoke_shell 进入菜单 → `/{ip}` 搜索 → 回车
    进入目标机 → 哨兵包裹执行命令。

    注意：目标机侧是交互式 shell，无退出码通道，用 `echo <SENTINEL>$?` 采集。
    """

    CONNECT_TIMEOUT = GateShellSession.CONNECT_TIMEOUT
    MAX_EXEC_TIMEOUT = 120  # 命令执行硬上限

    def __init__(self, ssh_config, on_progress=None):
        self.cfg = ssh_config
        self.on_progress = on_progress
        self._session = None
        self._stop_flag = threading.Event()

    # ── 对外回调 ──
    def _report(self, tag, text):
        if not isinstance(text, str):
            text = str(text)
        if tag in ("stdout", "stderr"):
            logger.debug("[ssh:%s] %s", tag, text.strip())
        else:
            logger.info("[ssh:%s] %s", tag, text.strip())
        if self.on_progress:
            try:
                self.on_progress(tag, text)
            except Exception as e:
                logger.warning("on_progress 回调异常: %s", e)

    def stop(self):
        self._stop_flag.set()
        logger.info("收到停止请求，正在关闭堡垒机会话")
        self.close()

    def close(self):
        if self._session is not None:
            self._session.close()
            self._session = None

    # ── 会话构建 ──
    def _build_session(self):
        cfg = self.cfg
        jump_h = (cfg.get("jump_host") or "").strip()
        jump_u = (cfg.get("jump_user") or "").strip()
        jump_pw = (cfg.get("jump_pass") or "").strip()
        if not all([jump_h, jump_u, jump_pw]):
            raise ExecutionError("跳板机配置不完整（主机/用户/密码）")
        return GateShellSession(
            jump_h, _port(cfg.get("jump_port"), 22), jump_u, jump_pw,
            on_log=lambda tag, text: self._report(tag, text),
            stop_event=self._stop_flag,
        )

    def run(self, command, timeout=30):
        cfg = self.cfg
        target_h = (cfg.get("target_host") or "").strip()
        if not target_h:
            raise ExecutionError("目标机配置不完整（主机）")

        # 远端 curl 自身也要带超时，防止目标机进程挂死（仅对 curl 命令生效）
        if (re.search(r"(^|[/\s])curl(\s|$)", command)
                and "--max-time" not in command
                and not re.search(r"(^|\s)-m\s*\d", command)):
            command = f"{command} --max-time {timeout}"
            logger.info("远端 curl 未设置 --max-time，自动追加 %ss", timeout)
        exec_deadline = min(timeout + 20, self.MAX_EXEC_TIMEOUT)

        logger.info("堡垒机执行开始: jump=%s@%s:%s → target=%s, timeout=%ss",
                    cfg.get("jump_user"), cfg.get("jump_host"), cfg.get("jump_port"),
                    target_h, timeout)

        self._stop_flag.clear()
        self._report("info", f"正在连接堡垒机 {cfg.get('jump_user')}@{cfg.get('jump_host')}:{cfg.get('jump_port')}")

        self._session = self._build_session()
        self._session.open()
        self._report("info", f"正在从授权列表中选择 {target_h} ...")
        self._session.connect_host(target_h)
        self._report("info", f"正在目标机执行请求（超时 {exec_deadline}s）...")

        exit_code, body = self._session.run(command, timeout=exec_deadline)
        return {"exit_code": exit_code, "stdout": body, "stderr": ""}

    # ── 连接测试 / 授权机器列表 ──
    @staticmethod
    def test_connection(cfg, include_target=False):
        """连接测试（阻塞，供工作线程调用）。

        include_target=False 只测堡垒机登录；True 再验证目标机能否进入。
        返回 (ok, message)
        """
        stop = threading.Event()
        sess = None
        try:
            jump_h = (cfg.get("jump_host") or "").strip()
            jump_u = (cfg.get("jump_user") or "").strip()
            jump_pw = (cfg.get("jump_pass") or "").strip()
            target_h = (cfg.get("target_host") or "").strip()

            if not all([jump_h, jump_u, jump_pw]):
                return False, "跳板机配置不完整（主机/用户/密码）"

            sess = GateShellSession(
                jump_h, _port(cfg.get("jump_port"), 22), jump_u, jump_pw,
                stop_event=stop,
            )
            sess.open()

            if not include_target:
                n = sess.total_hosts()
                tail = f"，授权机器 {n} 台" if n >= 0 else ""
                return True, f"堡垒机连接成功{tail}（{jump_u}@{jump_h}）"

            if not target_h:
                return False, "目标机配置不完整（主机）"

            hit = sess.find_host(target_h)
            if hit is None:
                return False, (f"授权列表中没有 {target_h}，"
                               f"请先在堡垒机申请该机器的权限")
            sess.connect_host(target_h)
            code, out = sess.run("hostname", timeout=25)
            if code == 0:
                label = hit["name"] or target_h
                return True, f"目标机连接成功：{label}（{hit['ip']}:{hit['port']}）"
            return False, f"目标机已连接但执行探测命令失败（exit={code}）{_tail(out)}"

        except (ExecutionError, ExecutionTimeout) as e:
            return False, str(e)
        except Exception as e:
            logger.exception("连接测试异常")
            return False, f"连接测试异常: {e}"
        finally:
            if sess is not None:
                sess.close()

    @staticmethod
    def list_authorized_hosts(cfg):
        """拉取堡垒机授权机器列表，返回 (ok, hosts_or_message)"""
        stop = threading.Event()
        sess = None
        try:
            jump_h = (cfg.get("jump_host") or "").strip()
            jump_u = (cfg.get("jump_user") or "").strip()
            jump_pw = (cfg.get("jump_pass") or "").strip()
            if not all([jump_h, jump_u, jump_pw]):
                return False, "跳板机配置不完整（主机/用户/密码）"
            sess = GateShellSession(
                jump_h, _port(cfg.get("jump_port"), 22), jump_u, jump_pw,
                stop_event=stop,
            )
            sess.open()
            return True, sess.list_hosts()
        except (ExecutionError, ExecutionTimeout) as e:
            return False, str(e)
        except Exception as e:
            logger.exception("拉取授权机器列表异常")
            return False, f"拉取授权机器列表异常: {e}"
        finally:
            # 无论成功失败都关闭，避免堡垒机侧堆积会话
            if sess is not None:
                sess.close()