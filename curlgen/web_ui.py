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
import sys
import threading
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
from .curl_builder import build_curl
from .ssh_executor import (
    LocalExecutor, SSHRemoteExecutor,
    ExecutionError, ExecutionTimeout, ExecutorStop,
)

logger = logging.getLogger("curlgen.web_ui")

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
                })
            elif path == "/api/envs":
                self._send_json(load_env_configs())
            elif path == "/api/ssh":
                self._send_json(normalize_ssh_config(load_ssh_config()))
            elif path == "/api/ssh_targets":
                self._send_json(load_ssh_targets())
            elif path == "/api/history":
                self._send_json(self.app.history)
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
            elif path == "/api/switch_legacy":
                self.app.sig_switch_legacy.emit()
                self._send_json({"ok": True})
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as e:
            logger.exception("POST %s 失败", self.path)
            self._send_json({"error": str(e)}, 500)

    # ── 业务 ──
    def _generate(self, data):
        try:
            curl, sign_preview = build_curl(data)
        except ValueError as e:
            self._send_json({"error": str(e)})
            return
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
            curl, _ = build_curl(data)
        except ValueError as e:
            self._send_json({"error": str(e)})
            return
        self.app.start_exec(data, curl, mode)
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
        html = html.replace('<link rel="stylesheet" href="app.css">', f"<style>\n{css}\n</style>")
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
        self.push_event({"type": "output", "tag": tag, "text": text})

    @staticmethod
    def _parse_timeout(data):
        t = (data.get("timeout") or "").strip()
        try:
            return max(1, int(t)) if t else 30
        except ValueError:
            return 30

    def start_exec(self, data, curl, mode):
        with self._exec_lock:
            if self._exec_process is not None:
                self.push_event({"type": "notify", "text": "已有任务在执行中", "kind": "busy"})
                return
        logger.info("开始执行请求 (mode=%s)", mode)

        ssh_cfg = None
        if mode == "ssh":
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
                    executor = LocalExecutor(on_progress=self._on_exec_progress)
                self._exec_process = executor
                try:
                    result = executor.run(curl, timeout=timeout)
                    code = result.get("exit_code", 0)
                    if code < 0 and result.get("stderr"):
                        self.push_event({"type": "output", "tag": "stderr",
                                         "text": f"[错误] {result['stderr'].strip()}"})
                    self.push_event({"type": "finished", "code": code})
                finally:
                    if mode == "ssh":
                        executor.close()
            except ExecutorStop as e:
                self.push_event({"type": "output", "tag": "stderr", "text": f"[错误] 执行已停止: {e}"})
                self.push_event({"type": "finished", "code": -2})
            except ExecutionTimeout as e:
                self.push_event({"type": "output", "tag": "stderr", "text": f"[错误] {e}"})
                self.push_event({"type": "finished", "code": -3})
            except ExecutionError as e:
                prefix = "SSH 失败" if mode == "ssh" else "执行失败"
                self.push_event({"type": "output", "tag": "stderr", "text": f"[错误] {prefix}: {e}"})
                self.push_event({"type": "finished", "code": -1})
            except Exception as e:
                logger.exception("执行异常")
                self.push_event({"type": "output", "tag": "stderr", "text": f"[错误] 执行异常: {e}"})
                self.push_event({"type": "finished", "code": -1})
            finally:
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
