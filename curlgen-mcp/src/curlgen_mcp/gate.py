"""GateShell menu-driven bastion session and command execution.

The desktop app already talks to GateShell. Its constraints are documented here
because they shape every remote tool:

* no SSH ``exec`` (the channel closes with empty stdout and exit status ``-1``);
* no raw TCP forwarding (``direct-tcpip`` is terminated by the bastion itself);
* the only entry point is the interactive menu (``invoke_shell``), where
  ``/{ip}`` searches and Enter enters the target machine.

Commands run in an interactive target shell, so completion is detected with
line-anchored START/END sentinels and the exit code is echoed after ``$?``.
"""

import logging
import re
import socket
import time
import uuid

from . import paths
from .transfer import GateTransfer

logger = logging.getLogger("curlgen_mcp.gate")

_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_ANSI_CHARSET = re.compile(r"\x1b[()][A-Za-z0-9]")
_ANSI_SINGLE = re.compile(r"\x1b[=>78MDEHc]")

# 001: name(remark)   10.0.0.1:22   ssh   root
_GATE_ROW_RE = re.compile(
    r"(\d{3}):\s+(.*?)\s+((?:\d{1,3}\.){3}\d{1,3}):(\d+)\s+(\S+)\s+(\S+)"
)
_GATE_STATUS_RE = re.compile(r"(\d+)/(\d+),\s*(\S+)")
_SHELL_PROMPT_RE = re.compile(r"\[[^\]\n]*\][#$][ \t]*$")


class GateError(Exception):
    """GateShell connection or command failure."""


class GateTimeout(GateError):
    """GateShell operation timed out."""


def strip_ansi(text):
    if not text:
        return ""
    text = _ANSI_OSC.sub("", text)
    text = _ANSI_CSI.sub("", text)
    text = _ANSI_CHARSET.sub("", text)
    text = _ANSI_SINGLE.sub("", text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_gate_hosts(text):
    """Parse authorized hosts from a GateShell menu screen."""
    hosts = {}
    for match in _GATE_ROW_RE.finditer(text):
        index, name, ip, port, proto, user = match.groups()
        hosts[index] = {
            "index": index,
            "name": name.strip(),
            "ip": ip,
            "port": int(port),
            "proto": proto,
            "user": user,
        }
    return [hosts[key] for key in sorted(hosts)]


def wrap_command(command, token=None):
    """Wrap ``command`` with line-anchored sentinels and capture ``$?``."""
    token = token or uuid.uuid4().hex[:10]
    start = f"__CG_S_{token}__"
    end = f"__CG_E_{token}__"
    wrapped = f"echo {start}; {command} 2>&1; __ec=$?; echo; echo {end}$__ec"
    return wrapped, start, end


def _extract_body(raw, start_mark, end_mark):
    text = strip_ansi(raw.decode("utf-8", "replace"))
    start = re.search(r"(?m)^" + re.escape(start_mark) + r"[ \t]*$", text)
    if not start:
        return text
    text = text[start.end():]
    end = re.search(r"(?m)^" + re.escape(end_mark) + r"\d+[ \t]*$", text)
    if end:
        text = text[:end.start()]
    return text


def _tail(text, n=200):
    text = " ".join((text or "").split())
    return f"\n远端输出：{text[-n:]}" if text else ""


def _port(value, default=22):
    try:
        return int(str(value).strip()) if str(value).strip() else default
    except (TypeError, ValueError):
        return default


class GateShellSession:
    """Interactive GateShell menu session.

    Lifecycle: ``open()`` → ``list_hosts()`` / ``connect_host()`` → ``run()`` →
    ``close()``. A session is not thread-safe; the MCP server serialises calls.
    """

    CONNECT_TIMEOUT = 10
    MENU_TIMEOUT = 25
    TYPE_DELAY = 0.1
    TARGET_TIMEOUT = 40
    PAGE_DOWN = b"\x1b[6~"

    def __init__(self, host, port, user, password, on_log=None, stop_event=None):
        self.host = host
        self.port = _port(port)
        self.user = user
        self.password = password
        self.on_log = on_log
        self.stop_event = stop_event
        self._transport = None
        self._chan = None
        self._in_target = False
        self._boot_text = ""

    def _log(self, text):
        logger.info("[gate] %s", text)
        if self.on_log:
            try:
                self.on_log("info", text)
            except Exception as exc:  # user callback must not break the session
                logger.warning("GateShell callback failed: %s", exc)

    def _check_stop(self):
        if self.stop_event is not None and self.stop_event.is_set():
            raise GateError("已手动停止")

    def open(self):
        import paramiko

        try:
            sock = socket.create_connection(
                (self.host, self.port), timeout=self.CONNECT_TIMEOUT
            )
        except socket.timeout:
            raise GateError(
                f"连接堡垒机超时（{self.CONNECT_TIMEOUT}s，请检查网络/地址）"
            ) from None
        except Exception as exc:
            raise GateError(f"连接堡垒机失败: {exc}") from exc

        try:
            sock.settimeout(self.CONNECT_TIMEOUT)
            self._transport = paramiko.Transport(sock)
            self._transport.start_client(timeout=self.CONNECT_TIMEOUT)
            self._transport.auth_password(self.user, self.password)
        except paramiko.ssh_exception.AuthenticationException as exc:
            self.close()
            raise GateError(f"堡垒机认证失败（用户名或密码错误）: {exc}") from exc
        except socket.timeout:
            self.close()
            raise GateError(f"连接堡垒机超时（{self.CONNECT_TIMEOUT}s）") from None
        except Exception as exc:
            self.close()
            raise GateError(f"连接堡垒机失败: {exc}") from exc

        self._log(f"堡垒机已连接 {self.user}@{self.host}:{self.port}")

        try:
            self._chan = self._transport.open_session(timeout=self.CONNECT_TIMEOUT)
            self._chan.settimeout(1.0)
            self._chan.get_pty(term="xterm", width=200, height=50)
            self._chan.invoke_shell()
        except Exception as exc:
            self.close()
            raise GateError(f"打开堡垒机会话失败（可能不是 GateShell）: {exc}") from exc

        raw, _ = self._pump(
            idle=4.0, hard_deadline=time.monotonic() + self.MENU_TIMEOUT
        )
        text = strip_ansi(raw.decode("utf-8", "replace"))
        self._boot_text = text
        if "Quit: Use" not in text and not _GATE_ROW_RE.search(text):
            self.close()
            raise GateError("堡垒机未返回授权机器菜单（可能不是 GateShell 类型）")
        self._log("已进入授权机器菜单")

    def _pump(self, idle=4.0, until=None, hard_deadline=None):
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
                        match = until.search(strip_ansi(raw.decode("utf-8", "replace")))
                        if match:
                            return raw, match
                else:
                    time.sleep(0.05)
            except socket.timeout:
                continue
            except (EOFError, OSError) as exc:
                logger.debug("GateShell channel ended: %s", exc)
                break
        match = (
            until.search(strip_ansi(raw.decode("utf-8", "replace")))
            if until
            else None
        )
        return raw, match

    def _drain(self, seconds=1.2):
        self._pump(idle=0.4, hard_deadline=time.monotonic() + seconds)

    def _type(self, text, delay=None):
        if self._chan is None or self._chan.closed:
            raise GateError("堡垒机会话已断开")
        if delay is None:
            delay = self.TYPE_DELAY
        if not delay:
            data = text.encode("utf-8")
            # Large base64 commands would take minutes character-by-character.
            for offset in range(0, len(data), 4096):
                self._check_stop()
                self._chan.send(data[offset:offset + 4096])
            return
        for char in text:
            self._check_stop()
            self._chan.send(char.encode("utf-8"))
            time.sleep(delay)

    def list_hosts(self, max_pages=12):
        boot = self._boot_text
        if not boot:
            raw, _ = self._pump(
                idle=4.0, hard_deadline=time.monotonic() + self.MENU_TIMEOUT
            )
            boot = strip_ansi(raw.decode("utf-8", "replace"))
            self._boot_text = boot
        hosts = {host["index"]: host for host in parse_gate_hosts(boot)}
        match = _GATE_STATUS_RE.search(boot)
        total = int(match.group(2)) if match else None

        for _ in range(max_pages):
            if total and len(hosts) >= total:
                break
            self._check_stop()
            self._chan.send(self.PAGE_DOWN)
            chunk, _ = self._pump(
                idle=3.0, hard_deadline=time.monotonic() + self.TARGET_TIMEOUT
            )
            before = len(hosts)
            for host in parse_gate_hosts(strip_ansi(chunk.decode("utf-8", "replace"))):
                hosts[host["index"]] = host
            if len(hosts) == before:
                break

        result = [hosts[key] for key in sorted(hosts)]
        self._log(
            f"已加载 {len(result)} 台授权机器"
            + (f" / 共 {total}" if total else "")
        )
        return result

    def find_host(self, target):
        for host in parse_gate_hosts(self._boot_text):
            if host["ip"] == target or host["name"] == target:
                return host
        for host in self.list_hosts():
            if host["ip"] == target or host["name"] == target:
                return host
        return None

    def total_hosts(self):
        match = _GATE_STATUS_RE.search(self._boot_text or "")
        return int(match.group(2)) if match else -1

    def connect_host(self, target):
        if self._in_target:
            return
        self._log(f"在菜单中搜索 {target} ...")
        self._type(f"/{target}")
        time.sleep(1.2)
        self._chan.send(b"\r")
        self._pump(idle=2.5, hard_deadline=time.monotonic() + 10)
        self._chan.send(b"\r")

        ready = f"__CG_READY_{uuid.uuid4().hex[:8]}__"
        deadline = time.monotonic() + self.TARGET_TIMEOUT
        time.sleep(1.0)
        self._type(f"echo {ready}", delay=0.02)
        self._chan.send(b"\r")
        raw, match = self._pump(
            until=re.compile(r"(?m)^" + re.escape(ready) + r"[ \t]*$"),
            idle=5.0,
            hard_deadline=deadline,
        )
        if not match:
            text = strip_ansi(raw.decode("utf-8", "replace")).strip()
            hint = ""
            low = text.lower()
            if "permission" in low or "denied" in low or "not authorized" in low:
                hint = "（堡垒机拒绝了该连接）"
            elif "not found" in low or "no such" in low:
                hint = "（菜单中未找到该机器）"
            self.close()
            raise GateError(
                f"进入目标机 {target} 失败{hint}，请确认机器在授权列表内。"
                + _tail(text)
            )
        self._in_target = True
        self._drain()
        self._log(f"已进入目标机 {target}")

    def run(self, command, timeout=30):
        if not self._in_target:
            raise GateError("尚未进入目标机，无法执行命令")
        wrapped, start_mark, end_mark = wrap_command(command)
        deadline = time.monotonic() + timeout + 10
        start_pat = re.compile(r"(?m)^" + re.escape(start_mark) + r"[ \t]*$")
        end_pat = re.compile(r"(?m)^" + re.escape(end_mark) + r"(\d+)[ \t]*$")

        self._log(f"执行: {command[:200]}")
        self._type(wrapped, delay=0)
        self._chan.send(b"\r")

        raw = b""

        def matched(pattern):
            return pattern.search(strip_ansi(raw.decode("utf-8", "replace")))

        while time.monotonic() < deadline and not matched(start_pat):
            chunk, _ = self._pump(until=start_pat, idle=5.0, hard_deadline=deadline)
            if chunk:
                raw += chunk
        if not matched(start_pat):
            self.close()
            raise GateTimeout(f"目标机未开始执行命令（>{timeout}s），已断开")

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
                if delta.strip() and self.on_log:
                    self.on_log("stdout", delta)

        if not match:
            self.close()
            raise GateTimeout(f"命令超时（>{timeout}s），已断开堡垒机会话")

        exit_code = int(match.group(1))
        raw_body = _extract_body(raw, start_mark, end_mark)
        if self.on_log and len(raw_body) > emitted and raw_body[emitted:].strip():
            self.on_log("stdout", raw_body[emitted:])
        body = raw_body.strip("\r\n")
        body = _SHELL_PROMPT_RE.sub("", body.rstrip()).rstrip()
        self._log(f"执行结束 exit_code={exit_code}")
        return exit_code, body

    # ── files ──
    def _transfer(self):
        # Keep each base64 command modest; the target side is an interactive
        # readline shell, not a non-interactive exec channel.
        chunk = min(paths.transfer_chunk_size(), 16 * 1024)
        return GateTransfer(
            self.run,
            max_bytes=paths.max_transfer_bytes(),
            chunk_size=chunk,
        )

    def upload(self, data, remote_path, progress=None):
        return self._transfer().upload(data, remote_path, progress=progress)

    def download(self, remote_path, progress=None):
        return self._transfer().download(remote_path, progress=progress)

    def close(self):
        for obj in (self._chan, self._transport):
            if obj is not None:
                try:
                    obj.close()
                except Exception as exc:
                    logger.debug("关闭 GateShell 对象失败: %s", exc)
        self._chan = None
        self._transport = None
        self._in_target = False

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def open_session(config, on_log=None, stop_event=None):
    """Create and open a GateShell session from a resolved SSH config."""
    host = str(config.get("jump_host") or "").strip()
    user = str(config.get("jump_user") or "").strip()
    password = str(config.get("jump_pass") or "")
    if not (host and user and password):
        raise GateError("跳板机配置不完整（主机/用户/密码）")
    session = GateShellSession(
        host,
        _port(config.get("jump_port"), 22),
        user,
        password,
        on_log=on_log,
        stop_event=stop_event,
    )
    session.open()
    return session