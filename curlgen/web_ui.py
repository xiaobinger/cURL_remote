"""新版 UI：星轨控制台（Web 引擎驱动）

架构：
  QWebEngineView 渲染 curlgen/web/ 下的 HTML/CSS/JS 前端，
  Python 端起本地 HTTP API 服务（127.0.0.1 随机端口）提供数据与执行能力，
  页面通过 window.API_BASE 与后端通信；执行输出经 /api/events 事件流推送。
旧版 PyQt UI（main_window.py）保持不变，菜单栏可互相切换。
"""
import json
import logging
import os
import re
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PyQt5.QtCore import Qt, pyqtSignal, QUrl
from PyQt5.QtGui import QColor, QIcon
from PyQt5.QtWidgets import QMainWindow
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage

from .config import (
    SCRIPT_DIR, load_env_configs, save_env_configs,
    load_ssh_config, save_ssh_config,
    load_saved_requests, save_saved_requests,
    normalize_ssh_config, load_ssh_targets,
    resolve_ssh_cfg, env_ssh_target_alias,
)
from .curl_builder import build_curl, resolve_request, render_curl
from .ai_client import load_ai_config, save_ai_config, generate_script
from .http_executor import DirectHttpExecutor
from .ssh_executor import (
    SSHRemoteExecutor,
    ExecutionError, ExecutionTimeout, ExecutorStop,
)

logger = logging.getLogger("curlgen.web_ui")

# SSH 远程 curl -w 尾注标记（解析后不展示在输出区）
SSH_META_MARK = "__CG_META__"
SSH_META_RE = re.compile(r"__CG_META__:(\d{3}):([\d.]+):(\d+):([^\r\n]*)")
SSH_META_FMT = ' -w "\\n__CG_META__:%{http_code}:%{time_total}:%{size_download}:%{content_type}"'

# Web 前端资源目录（打包后位于 sys._MEIPASS/curlgen/web）
if getattr(sys, "frozen", False):
    WEB_DIR = os.path.join(sys._MEIPASS, "curlgen", "web")
else:
    WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


class _ApiHandler(BaseHTTPRequestHandler):
    """本地 API：所有路由返回 JSON。app 由服务器启动前注入。"""
    app = None

    # ── 基础设施 ──
    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}

    def log_message(self, fmt, *args):
        logger.debug("api %s - %s", self.address_string(), fmt % args)

    # ── 路由 ──
    def do_GET(self):
        try:
            path = self.path.split("?")[0]
            if path == "/api/state":
                ssh = normalize_ssh_config(load_ssh_config())
                ssh_ready = bool(
                    (ssh.get("jump_host") or "").strip()
                    and (ssh.get("jump_user") or "").strip()
                    and (ssh.get("jump_pass") or "").strip()
                    and ssh.get("targets")
                )
                self._send_json({
                    "logo": self.app.logo_data_url(),
                    "envs": load_env_configs(),
                    "saved": load_saved_requests(),
                    "ssh": {k: v for k, v in ssh.items() if k != "jump_pass"},
                    "ssh_ready": ssh_ready,
                    "ai": load_ai_config(),
                })
            elif path == "/api/envs":
                self._send_json(load_env_configs())
            elif path == "/api/ssh":
                self._send_json(normalize_ssh_config(load_ssh_config()))
            elif path == "/api/ssh_targets":
                self._send_json(load_ssh_targets())
            elif path == "/api/history":
                self._send_json(self.app.history)
            elif path == "/api/onthisday":
                self._on_this_day()
            elif path == "/api/events":
                query = self.path.split("?", 1)[1] if "?" in self.path else ""
                since = 0
                for part in query.split("&"):
                    if part.startswith("since="):
                        try:
                            since = int(part[6:])
                        except ValueError:
                            since = 0
                self._send_json({
                    "events": self.app.events[since:],
                    "next": len(self.app.events),
                })
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as e:
            logger.exception("GET %s 失败", self.path)
            self._send_json({"error": str(e)}, 500)

    def do_POST(self):
        try:
            path = self.path.split("?")[0]
            data = self._read_json()
            if path == "/api/generate":
                self._generate(data)
            elif path == "/api/execute":
                self._execute(data)
            elif path == "/api/stop":
                self.app.stop_exec()
                self._send_json({"ok": True})
            elif path == "/api/copy":
                from PyQt5.QtWidgets import QApplication
                QApplication.clipboard().setText(data.get("text", ""))
                self._send_json({"ok": True})
            elif path == "/api/save_request":
                self._save_request(data)
            elif path == "/api/import_requests":
                self._import_requests(data)
            elif path == "/api/delete_request":
                self._delete_request(data)
            elif path == "/api/clear_history":
                self.app.history.clear()
                self._send_json({"ok": True})
            elif path == "/api/save_envs":
                save_env_configs(data if isinstance(data, list) else [])
                self._send_json({"ok": True})
            elif path == "/api/save_ssh":
                save_ssh_config(normalize_ssh_config(data))
                self._send_json({"ok": True})
            elif path == "/api/test_ssh":
                self.app.start_ssh_test(bool(data.get("target")), data.get("label"))
                self._send_json({"ok": True})
            elif path == "/api/save_ai":
                self._send_json({"ok": True, "ai": save_ai_config(data)})
            elif path == "/api/ai_script":
                try:
                    script = generate_script(
                        load_ai_config(),
                        data.get("kind", "pre"),
                        (data.get("prompt") or "").strip(),
                        data.get("context") or {},
                    )
                except ValueError as e:
                    self._send_json({"error": str(e)})
                    return
                self._send_json({"ok": True, "script": script})
            elif path == "/api/switch_legacy":
                self.app.sig_switch_legacy.emit()
                self._send_json({"ok": True})
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as e:
            logger.exception("POST %s 失败", self.path)
            self._send_json({"error": str(e)}, 500)

    # ── 业务 ──
    def _on_this_day(self):
        """历史上的今天：代理中文维基百科 onthisday（避免前端跨域，离线时前端隐藏区块）"""
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        try:
            month = int(params.get("month", ""))
            day = int(params.get("day", ""))
            if not (1 <= month <= 12 and 1 <= day <= 31):
                raise ValueError
        except ValueError:
            self._send_json({"error": "invalid month/day"}, 400)
            return
        url = (f"https://api.wikimedia.org/feed/v1/wikipedia/zh/"
               f"onthisday/all/{month:02d}/{day:02d}")
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "curlgen-dashboard/2.0 (local tool)"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            events = [
                {"year": e.get("year"), "text": e.get("text", "")}
                for e in data.get("selected", []) + data.get("events", [])
                if e.get("text")
            ][:12]
            self._send_json({"events": events})
        except Exception as e:
            logger.info("onthisday 获取失败: %s", e)
            self._send_json({"error": str(e)})

    def _generate(self, data):
        try:
            curl, sign_preview = build_curl(data)
        except ValueError as e:
            self._send_json({"error": str(e)})
            return
        if not data.get("preview"):
            self.app.push_history(data, curl)
        self._send_json({"curl": curl, "sign_preview": sign_preview})

    def _save_request(self, payload):
        group_name = (payload.get("group") or "").strip()
        name = (payload.get("name") or "").strip()
        if not group_name or not name:
            self._send_json({"error": "分组和名称不能为空"})
            return
        saved = load_saved_requests()
        group = next((g for g in saved["groups"] if g["name"] == group_name), None)
        if group is None:
            group = {"name": group_name, "collapsed": False, "requests": []}
            saved["groups"].append(group)
        req_data = dict(payload.get("data") or {})
        req_data["name"] = name
        update_old = payload.get("update_old") or ""
        if update_old and update_old != name:
            group["requests"] = [r for r in group["requests"] if r.get("name") != update_old]
        group["requests"] = [r for r in group["requests"] if r.get("name") != name]
        group["requests"].append(req_data)
        group["collapsed"] = False
        save_saved_requests(saved)
        logger.info("保存快捷请求: %s/%s", group_name, name)
        self._send_json({"ok": True, "saved": saved})

    def _import_requests(self, payload):
        """批量导入（Postman / OpenAPI 解析后的结构）：同名片段合并、同名请求覆盖"""
        saved = load_saved_requests()
        g_new = 0
        r_new = 0
        for g in payload.get("groups") or []:
            gname = (g.get("name") or "").strip() or "导入"
            group = next((x for x in saved["groups"] if x["name"] == gname), None)
            if group is None:
                group = {"name": gname, "collapsed": False, "requests": []}
                saved["groups"].append(group)
                g_new += 1
            for r in g.get("requests") or []:
                if not (r.get("name") or "").strip():
                    continue
                group["requests"] = [x for x in group["requests"] if x.get("name") != r["name"]]
                group["requests"].append(r)
                r_new += 1
            group["collapsed"] = False
        save_saved_requests(saved)
        logger.info("导入请求: +%d 分组 +%d 请求", g_new, r_new)
        self._send_json({"ok": True, "saved": saved,
                         "groups_added": g_new, "requests_added": r_new})

    def _delete_request(self, payload):
        saved = load_saved_requests()
        for g in saved.get("groups", []):
            if g["name"] == payload.get("group"):
                g["requests"] = [r for r in g["requests"] if r.get("name") != payload.get("name")]
                break
        save_saved_requests(saved)
        self._send_json({"ok": True, "saved": saved})

    def _execute(self, payload):
        data = payload.get("data") or {}
        mode = payload.get("mode") or "local"
        try:
            req = resolve_request(data)
            curl = render_curl(req, silent=bool(data.get("silent")))
        except ValueError as e:
            self._send_json({"error": str(e)})
            return
        self.app.push_history(data, curl)
        self.app.start_exec(data, curl, mode, req)
        self._send_json({"ok": True})


class WebCurlGenerator(QMainWindow):
    """星轨控制台主窗口：WebEngine + 本地 API"""
    sig_switch_legacy = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("cURL Generator · 星轨控制台")
        self.resize(1400, 880)
        self.setMinimumSize(1080, 720)

        logo_path = os.path.join(SCRIPT_DIR, "logo.png")
        if os.path.exists(logo_path):
            self.setWindowIcon(QIcon(logo_path))

        # ── 本地 API 服务 ──
        self.history = []          # [{time, method, url, curl}]
        self.events = []           # 事件流（执行输出 / 通知 / 测试结果）
        self._exec_process = None
        self._exec_lock = threading.Lock()
        self._exec_mode = None     # 当前执行模式（local/ssh），供输出回调区分
        self._ssh_hold = ""        # SSH stdout 末行驻留缓冲（剥离 -w 尾注）

        _ApiHandler.app = self
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _ApiHandler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True,
                         name="curlgen-api").start()
        logger.info("本地 API 服务已启动: 127.0.0.1:%d", self.port)

        # ── WebEngine ──
        self.view = QWebEngineView()
        page = QWebEnginePage(self.view)
        page.setBackgroundColor(QColor("#070b16"))
        self.view.setPage(page)
        self.setCentralWidget(self.view)
        self._build_menubar()

        html_path = os.path.join(WEB_DIR, "index.html")
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        # setHtml 文档无法通过 file:// 加载外部资源，直接把 CSS/JS 内联
        with open(os.path.join(WEB_DIR, "app.css"), "r", encoding="utf-8") as f:
            css = f.read()
        with open(os.path.join(WEB_DIR, "app.js"), "r", encoding="utf-8") as f:
            js = f.read()
        with open(os.path.join(WEB_DIR, "vendor", "lunar.js"), "r", encoding="utf-8") as f:
            lunar_js = f.read()
        html = html.replace('<link rel="stylesheet" href="app.css">', f"<style>\n{css}\n</style>")
        html = html.replace('<script src="vendor/lunar.js"></script>', f"<script>\n{lunar_js}\n</script>")
        html = html.replace('<script src="app.js"></script>', f"<script>\n{js}\n</script>")
        html = html.replace("__PORT__", str(self.port))
        self.view.page().setHtml(html, QUrl.fromLocalFile(WEB_DIR.replace("\\", "/") + "/"))

        self.sig_switch_legacy.connect(self._open_legacy)

    # ──────────────────── 菜单栏 ────────────────────
    def _build_menubar(self):
        menubar = self.menuBar()

        view_menu = menubar.addMenu("  视图  ")

        legacy_action = view_menu.addAction("↩ 返回旧版界面")
        legacy_action.triggered.connect(self._open_legacy)

        view_menu.addSeparator()

        reload_action = view_menu.addAction("⟳ 刷新页面")
        reload_action.triggered.connect(self.view.reload)

        exit_action = view_menu.addAction("✕ 退出")
        exit_action.triggered.connect(self.close)

    def _on_console(self, level, message, line, source):
        logger.debug("web console [%s:%d] %s", os.path.basename(source or ""), line, message)

    # ──────────────────── 事件流 ────────────────────
    def logo_data_url(self):
        """logo.png → base64 data URL（避免 file:// 图片跨目录加载问题）"""
        import base64
        path = os.path.join(SCRIPT_DIR, "logo.png")
        if not os.path.exists(path):
            return ""
        with open(path, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode()

    def push_event(self, ev):
        self.events.append(ev)

    def push_history(self, data, curl):
        from datetime import datetime
        self.history.insert(0, {
            "time": datetime.now().strftime("%H:%M:%S"),
            "method": data.get("method", "GET"),
            "url": data.get("url", ""),
            "curl": curl,
        })
        if len(self.history) > 50:
            self.history.pop()

    # ──────────────────── 执行 ────────────────────
    def _on_exec_progress(self, tag, text):
        # SSH 模式为剥离 curl -w 尾注：stdout 驻留最后一行缓冲，执行结束时统一处理
        if self._exec_mode == "ssh" and tag == "stdout":
            self._ssh_hold += text
            idx = self._ssh_hold.rfind("\n")
            if idx >= 0:
                out, self._ssh_hold = self._ssh_hold[:idx + 1], self._ssh_hold[idx + 1:]
                if out:
                    self.push_event({"type": "output", "tag": tag, "text": out})
            return
        self.push_event({"type": "output", "tag": tag, "text": text})

    def _flush_ssh_hold(self):
        """SSH 执行结束：解析驻留缓冲里的 -w 尾注，推送 http_resp 事件并回显剩余内容"""
        hold, self._ssh_hold = self._ssh_hold, ""
        if not hold:
            return
        m = SSH_META_RE.search(hold)
        if m:
            hold = SSH_META_RE.sub("", hold).strip("\r\n")
            try:
                self.push_event({
                    "type": "http_resp",
                    "status": int(m.group(1)),
                    "reason": "",
                    "headers": [],
                    "elapsed_ms": int(float(m.group(2)) * 1000),
                    "size": int(m.group(3)),
                    "content_type": m.group(4).strip(),
                    "url": "",
                })
            except ValueError:
                logger.warning("SSH 尾注解析失败: %s", m.group(0))
        if hold:
            self.push_event({"type": "output", "tag": "stdout", "text": hold})

    @staticmethod
    def _parse_timeout(data):
        t = (data.get("timeout") or "").strip()
        try:
            return max(1, int(t)) if t else 30
        except ValueError:
            return 30

    def start_exec(self, data, curl, mode, req=None):
        with self._exec_lock:
            if self._exec_process is not None:
                self.push_event({"type": "notify", "text": "已有任务在执行中", "kind": "busy"})
                return
        logger.info("开始执行请求 (mode=%s)", mode)
        self._exec_mode = mode
        self._ssh_hold = ""

        ssh_cfg = None
        if mode == "ssh":
            # 远程 curl 追加 -w 尾注，用于解析 HTTP 状态/耗时/体积（前端不展示）
            curl = curl + SSH_META_FMT
            # 按当前环境解析 SSH 目标机（环境启用 SSH 时用其绑定标签，否则用第一个目标机）
            env_alias = data.get("env")
            label = env_ssh_target_alias(env_alias)
            ssh_cfg = resolve_ssh_cfg(load_ssh_config(), label)
            ok = all([
                (ssh_cfg.get("jump_host") or "").strip(),
                (ssh_cfg.get("jump_user") or "").strip(),
                (ssh_cfg.get("jump_pass") or "").strip(),
                (ssh_cfg.get("target_host") or "").strip(),
            ])
            if not ok:
                msg = "SSH 配置不完整，请在「SSH 设置」中完善跳板机与目标机信息"
                self.push_event({"type": "output", "tag": "stderr",
                                 "text": f"──── 状态: 配置不完整 ────\n[错误] {msg}"})
                self.push_event({"type": "notify", "text": msg, "kind": "err"})
                self.push_event({"type": "finished", "code": -1})
                return

        def worker():
            try:
                timeout = self._parse_timeout(data)
                if mode == "ssh":
                    executor = SSHRemoteExecutor(ssh_cfg, on_progress=self._on_exec_progress)
                else:
                    # 本地直发：直接用 Python 发 HTTP，不再 shell 调 curl
                    executor = DirectHttpExecutor(on_progress=self._on_exec_progress,
                                                  on_event=self.push_event)
                self._exec_process = executor
                try:
                    if mode == "ssh":
                        result = executor.run(curl, timeout=timeout)
                    else:
                        result = executor.run(req, timeout=timeout)
                    code = result.get("exit_code", 0)
                    if code < 0 and result.get("stderr"):
                        self.push_event({"type": "output", "tag": "stderr",
                                         "text": f"[错误] {result['stderr'].strip()}"})
                    self._flush_ssh_hold()
                    self.push_event({"type": "finished", "code": code})
                finally:
                    if mode == "ssh":
                        executor.close()
            except ExecutorStop as e:
                self._flush_ssh_hold()
                self.push_event({"type": "output", "tag": "stderr", "text": f"[错误] 执行已停止: {e}"})
                self.push_event({"type": "finished", "code": -2})
            except ExecutionTimeout as e:
                self._flush_ssh_hold()
                self.push_event({"type": "output", "tag": "stderr", "text": f"[错误] {e}"})
                self.push_event({"type": "finished", "code": -3})
            except ExecutionError as e:
                self._flush_ssh_hold()
                prefix = "SSH 失败" if mode == "ssh" else "执行失败"
                self.push_event({"type": "output", "tag": "stderr", "text": f"[错误] {prefix}: {e}"})
                self.push_event({"type": "finished", "code": -1})
            except Exception as e:
                logger.exception("执行异常")
                self._flush_ssh_hold()
                self.push_event({"type": "output", "tag": "stderr", "text": f"[错误] 执行异常: {e}"})
                self.push_event({"type": "finished", "code": -1})
            finally:
                self._exec_mode = None
                with self._exec_lock:
                    self._exec_process = None

        threading.Thread(target=worker, daemon=True, name="curlgen-exec").start()

    def stop_exec(self):
        with self._exec_lock:
            proc = self._exec_process
        if proc is not None:
            try:
                proc.stop()
                logger.info("已请求停止执行")
            except Exception as e:
                logger.warning("停止执行失败: %s", e)

    # ──────────────────── SSH 测试 ────────────────────
    def start_ssh_test(self, include_target, label=None):
        def worker():
            cfg = resolve_ssh_cfg(load_ssh_config(), label)
            try:
                ok, msg = SSHRemoteExecutor.test_connection(cfg, include_target=include_target)
            except Exception as e:
                ok, msg = False, str(e)
            self.push_event({
                "type": "ssh_test",
                "is_target": "target" if include_target else "jump",
                "ok": bool(ok), "msg": msg,
            })
        threading.Thread(target=worker, daemon=True, name="curlgen-sshtest").start()

    # ──────────────────── 切换 / 关闭 ────────────────────
    def _open_legacy(self):
        if getattr(self, "_legacy_opened", False):
            return
        self._legacy_opened = True
        from .main_window import CurlGenerator
        logger.info("切换回旧版界面")
        self._legacy_win = CurlGenerator()
        self._legacy_win.show()
        self.close()

    def closeEvent(self, event):
        if getattr(self, "_server_closed", False):
            super().closeEvent(event)
            return
        self._server_closed = True
        self._server.shutdown()
        super().closeEvent(event)
