"""星轨控制台（Web 引擎驱动）

架构：
  QWebEngineView 渲染 curlgen/web/ 下的 HTML/CSS/JS 前端，
  Python 端起本地 HTTP API 服务（127.0.0.1 随机端口）提供数据与执行能力，
  页面通过 window.API_BASE 与后端通信；执行输出经 /api/events 事件流推送。
"""
import hmac
import json
import logging
import os
import re
import secrets
import sys
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QColor, QIcon
from PyQt5.QtWidgets import QMainWindow
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage

from .config import (
    SCRIPT_DIR, load_env_configs, save_env_configs,
    load_ssh_config, save_ssh_config,
    load_saved_requests, save_saved_requests,
    load_todos, save_todos,
    load_stocks, save_stocks,
    load_ui_prefs, save_ui_prefs, normalize_dashboard_layout,
    normalize_ssh_config, load_ssh_targets,
    resolve_ssh_cfg, env_ssh_target_alias,
)
from .curl_builder import build_curl, resolve_request, render_curl
from .code_gen import render_code
from .ai_client import load_ai_config, save_ai_config, generate_script
from .http_executor import DirectHttpExecutor
from .ssh_executor import (
    SSHRemoteExecutor,
    ExecutionError, ExecutionTimeout, ExecutorStop,
)
from .terminal_manager import TerminalManager

logger = logging.getLogger("curlgen.web_ui")


def terminal_token_valid(expected, supplied):
    return bool(expected and supplied and hmac.compare_digest(
        expected.encode("utf-8"), supplied.encode("utf-8")))


# /api/ui_prefs 读-改-写串行化（ThreadingHTTPServer 并发请求会丢更新）
_UI_PREFS_LOCK = threading.Lock()

# SSH 远程 curl -w 尾注标记（解析后不展示在输出区）
SSH_META_MARK = "__CG_META__"
SSH_META_RE = re.compile(r"__CG_META__:(\d{3}):([\d.]+):(\d+):([^\r\n]*)")
SSH_META_FMT = ' -w "\\n__CG_META__:%{http_code}:%{time_total}:%{size_download}:%{content_type}"'

# 内置法定节假日/调休表（国务院公布；timor.tech 在线数据离线兜底用，跨年时补充下一年）
# 值："休"=公休日（含调休形成的连休），"班"=调休补班日
BUILTIN_HOLIDAYS = {
    2025: {
        "2025-01-01": "休",
        "2025-01-26": "班", "2025-01-28": "休", "2025-01-29": "休", "2025-01-30": "休",
        "2025-01-31": "休", "2025-02-01": "休", "2025-02-02": "休", "2025-02-03": "休",
        "2025-02-04": "休", "2025-02-08": "班",
        "2025-04-04": "休", "2025-04-05": "休", "2025-04-06": "休", "2025-04-27": "班",
        "2025-05-01": "休", "2025-05-02": "休", "2025-05-03": "休", "2025-05-04": "休",
        "2025-05-05": "休",
        "2025-05-31": "休", "2025-06-01": "休", "2025-06-02": "休",
        "2025-09-28": "班",
        "2025-10-01": "休", "2025-10-02": "休", "2025-10-03": "休", "2025-10-04": "休",
        "2025-10-05": "休", "2025-10-06": "休", "2025-10-07": "休", "2025-10-08": "休",
        "2025-10-11": "班",
    },
    2026: {
        "2026-01-01": "休", "2026-01-02": "休", "2026-01-03": "休", "2026-01-04": "班",
        "2026-02-14": "班", "2026-02-15": "休", "2026-02-16": "休", "2026-02-17": "休",
        "2026-02-18": "休", "2026-02-19": "休", "2026-02-20": "休", "2026-02-21": "休",
        "2026-02-22": "休", "2026-02-23": "休", "2026-02-28": "班",
        "2026-04-04": "休", "2026-04-05": "休", "2026-04-06": "休",
        "2026-05-01": "休", "2026-05-02": "休", "2026-05-03": "休", "2026-05-04": "休",
        "2026-05-05": "休", "2026-05-09": "班",
        "2026-06-19": "休", "2026-06-20": "休", "2026-06-21": "休",
        "2026-09-20": "班", "2026-09-25": "休", "2026-09-26": "休", "2026-09-27": "休",
        "2026-10-01": "休", "2026-10-02": "休", "2026-10-03": "休", "2026-10-04": "休",
        "2026-10-05": "休", "2026-10-06": "休", "2026-10-07": "休", "2026-10-10": "班",
    },
}

# Web 前端资源目录（打包后位于 sys._MEIPASS/curlgen/web）
if getattr(sys, "frozen", False):
    WEB_DIR = os.path.join(sys._MEIPASS, "curlgen", "web")
else:
    WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def _normalize_stock_code(raw):
    """统一股票代码为带市场前缀形式：sh600519 / sz000001 / sh000001（指数）。
    纯 6 位数字按规则推断市场：6→沪，0/3→深；已带 sh/sz 前缀原样转小写。
    无法识别返回 None。"""
    s = (raw or "").strip().lower()
    if re.match(r"^(sh|sz)\d{6}$", s):
        return s
    if re.match(r"^\d{6}$", s):
        if s[0] == "6":
            return "sh" + s
        if s[0] in ("0", "3"):
            return "sz" + s
    return None


def _secid(code):
    """腾讯风格代码（sh600519）→ 东财 secid（1.600519 / 0.000001）"""
    return ("1." if code.startswith("sh") else "0.") + code[2:]


def _fetch_kline_days(start, end, code="sh000001"):
    """日 K 双源自愈：东财优先，断连/反爬时回落腾讯。
    start/end 为 YYYY-MM-DD；返回 [{date, open, close, high, low, vol}]（按日期升序）。"""
    s, e = start.replace("-", ""), end.replace("-", "")
    try:
        req = urllib.request.Request(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            f"?secid={_secid(code)}&klt=101&fqt=0&beg={s}&end={e}"
            "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                     "Referer": "https://quote.eastmoney.com/"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        days = []
        for line in ((data.get("data") or {}).get("klines")) or []:
            p = line.split(",")
            if len(p) >= 6:
                days.append({"date": p[0], "open": float(p[1]), "close": float(p[2]),
                             "high": float(p[3]), "low": float(p[4]), "vol": float(p[5])})
        if days:
            return days
        raise ValueError("empty klines")
    except Exception as ex:
        logger.info("东财日K失败，回落腾讯 %s (%s~%s): %s", code, start, end, ex)
    req = urllib.request.Request(
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={code},day,{start},{end},400,qfq",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                 "Referer": "https://gu.qq.com/"})
    with urllib.request.urlopen(req, timeout=6) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    # 指数返回 day，个股前复权返回 qfqday（qfq 以最新价为锚，尾部与实时价一致）
    d = (data.get("data") or {}).get(code) or {}
    raw = d.get("day") or d.get("qfqday") or d.get("hfqday") or []
    days = []
    for p in raw:
        if len(p) >= 6:
            days.append({"date": p[0], "open": float(p[1]), "close": float(p[2]),
                         "high": float(p[3]), "low": float(p[4]), "vol": float(p[5])})
    return days


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

    def _serve_static(self, path):
        """前端静态资源：/ → index.html（替换 __PORT__），其余从 WEB_DIR 按白名单读取。
        禁止路径穿越；仅本地随机端口提供服务。"""
        rel = (path or "/").lstrip("/") or "index.html"
        if ".." in rel or "\\" in rel:
            self._send_json({"error": "not found"}, 404)
            return
        full = os.path.join(WEB_DIR, rel)
        if not os.path.isfile(full):
            self._send_json({"error": "not found"}, 404)
            return
        mime = self._STATIC_MIME.get(os.path.splitext(rel)[1].lower(),
                                     "application/octet-stream")
        try:
            with open(full, "rb") as f:
                content = f.read()
            if rel == "index.html":
                content = content.decode("utf-8").replace(
                    "__PORT__", str(self.app.port)).replace(
                    "__TERMINAL_TOKEN__", self.app.terminal_token).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            logger.error("静态资源读取失败 %s: %s", rel, e)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}

    def _read_terminal_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def log_message(self, fmt, *args):
        logger.debug("api %s - %s", self.address_string(), fmt % args)

    # ── 路由 ──
    # 静态资源 MIME（页面经本地 HTTP 加载；setHtml 有体积上限，内嵌 ECharts 后超限会黑屏）
    _STATIC_MIME = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".png": "image/png",
    }

    def do_GET(self):
        try:
            path = self.path.split("?")[0]
            if path.startswith("/api/terminal/") and not terminal_token_valid(
                    self.app.terminal_token, self.headers.get("X-CurlGen-Token")):
                self._send_json({"error": "forbidden"}, 403)
                return
            if not path.startswith("/api/"):
                self._serve_static(path)
                return
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
                    "stocks": load_stocks(),
                    "ui": load_ui_prefs(),
                })
            elif path == "/api/envs":
                self._send_json(load_env_configs())
            elif path == "/api/ssh":
                self._send_json(normalize_ssh_config(load_ssh_config()))
            elif path == "/api/ssh_targets":
                self._send_json(load_ssh_targets())
            elif path == "/api/history":
                self._send_json(self.app.history)
            elif path == "/api/holidays":
                self._holidays()
            elif path == "/api/astock":
                self._astock()
            elif path == "/api/astock_kline":
                self._astock_month_kline()
            elif path == "/api/todos":
                self._send_json({"todos": load_todos()})
            elif path == "/api/stocks":
                self._send_json({"stocks": load_stocks()})
            elif path == "/api/stock_lookup":
                self._stock_lookup()
            elif path == "/api/terminal/events":
                self._terminal_events()
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
            if path.startswith("/api/terminal/") and not terminal_token_valid(
                    self.app.terminal_token, self.headers.get("X-CurlGen-Token")):
                self._send_json({"error": "forbidden"}, 403)
                return
            if path.startswith("/api/terminal/"):
                data = self._read_terminal_json()
                if data is None:
                    self._send_json({"error": "invalid json"}, 400)
                    return
            else:
                data = self._read_json()
            if path == "/api/terminal/open":
                self._send_json(self._terminal_open(data))
            elif path == "/api/terminal/input":
                self.app.terminals.write(data.get("id") or "", data.get("text") or "")
                self._send_json({"ok": True})
            elif path == "/api/terminal/interrupt":
                self.app.terminals.interrupt(data.get("id") or "")
                self._send_json({"ok": True})
            elif path == "/api/terminal/close":
                self.app.terminals.close(data.get("id") or "")
                self._send_json({"ok": True})
            elif path == "/api/generate":
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
            elif path == "/api/todos":
                self._save_todo(data)
            elif path == "/api/stocks":
                self._save_stock(data)
            elif path == "/api/ui_prefs":
                with _UI_PREFS_LOCK:
                    prefs = load_ui_prefs()
                    for k in ("theme", "stock"):  # 白名单合并，防任意键写入
                        if isinstance(data.get(k), str) and data[k]:
                            prefs[k] = data[k]
                    if "dashboard" in data:
                        prefs["dashboard"] = normalize_dashboard_layout(data["dashboard"])
                    save_ui_prefs(prefs)
                self._send_json({"ok": True, "ui": prefs})
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
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as e:
            logger.exception("POST %s 失败", self.path)
            self._send_json({"error": str(e)}, 500)

    # ── 业务 ──
    def _terminal_open(self, payload):
        kind = payload.get("kind") or "local"
        session = self.app.terminals.open(
            kind=kind,
            label=payload.get("label") or None,
            target_index=payload.get("target_index"),
            jump=bool(payload.get("jump")) if kind == "ssh" else False,
        )
        return {
            key: session[key] for key in ("id", "kind", "label", "status", "jump")
            if key in session
        }

    def _terminal_events(self):
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = urllib.parse.parse_qs(query, keep_blank_values=True)
        raw_since = params.get("since", ["0"])[0]
        if not re.fullmatch(r"[0-9]+", raw_since):
            self._send_json({"error": "invalid since"}, 400)
            return
        try:
            since = int(raw_since)
        except ValueError:
            self._send_json({"error": "invalid since"}, 400)
            return
        session_id = params.get("id", [""])[0]
        self._send_json(self.app.terminals.events(session_id, since))

    def _holidays(self):
        """法定节假日/调休：代理 timor.tech 年度数据（holiday=true→休, false→班），
        失败时回落到内置数据（BUILTIN_HOLIDAYS）。结果按年缓存。"""
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        try:
            year = int(params.get("year", ""))
            if not (2000 <= year <= 2100):
                raise ValueError
        except ValueError:
            self._send_json({"error": "invalid year"}, 400)
            return
        cache = self.app._holiday_cache
        if year in cache:
            self._send_json({"map": cache[year]})
            return
        try:
            req = urllib.request.Request(
                f"https://timor.tech/api/holiday/year/{year}",
                headers={"User-Agent": "curlgen-dashboard/2.0 (local tool)"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            m = {}
            for entry in (data.get("holiday") or {}).values():
                d = entry.get("date")
                if d:
                    m[d] = "休" if entry.get("holiday") else "班"
            if not m:
                raise ValueError("empty holiday data")
            cache[year] = m
            self._send_json({"map": m})
        except Exception as e:
            logger.info("节假日在线获取失败，回落内置数据: %s", e)
            m = BUILTIN_HOLIDAYS.get(year, {})
            cache[year] = m
            self._send_json({"map": m, "fallback": True})

    def _astock(self):
        """某日行情：历史走日 K 双源，当天走腾讯实时行情。code 缺省 sh000001（上证指数）。
        返回 {"trading": bool, "open", "close", "pct", "live"}，非交易日 trading=false。"""
        import datetime
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        date = (params.get("date") or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            self._send_json({"error": "invalid date"}, 400)
            return
        code = _normalize_stock_code(params.get("code") or "sh000001")
        if not code:
            self._send_json({"error": "invalid code"}, 400)
            return
        today = datetime.date.today().isoformat()
        try:
            if date >= today:
                self._send_json(self._astock_live(date, code))
            else:
                self._send_json(self._astock_kline(date, code))
        except Exception as e:
            logger.info("A股行情获取失败 %s %s: %s", code, date, e)
            self._send_json({"error": str(e)})

    @staticmethod
    def _astock_kline(date, code="sh000001"):
        """历史某天：日 K 双源自愈（东财→腾讯）；无 K 线即非交易日。
        pct 用前一交易日收盘计算，兼容两个源。"""
        import datetime
        d0 = datetime.date.fromisoformat(date) - datetime.timedelta(days=15)
        days = _fetch_kline_days(d0.isoformat(), date, code)
        idx = next((i for i, d in enumerate(days) if d["date"] == date), None)
        if idx is None:
            return {"trading": False}
        d = days[idx]
        prev = days[idx - 1]["close"] if idx > 0 else d["open"]
        pct = round((d["close"] - prev) / prev * 100, 2) if prev else 0.0
        return {"trading": True, "live": False,
                "open": d["open"], "close": d["close"], "pct": pct}

    @staticmethod
    def _astock_live(date, code="sh000001"):
        """当天：腾讯实时行情（v_sh000001，~ 分隔；3 现价 4 昨收 5 今开 6 成交量(手)
        30 时间戳 33 最高 34 最低）。
        时间戳日期与请求日不一致说明当天非交易日（接口返回的是最近交易日数据）。"""
        req = urllib.request.Request(
            f"https://qt.gtimg.cn/q={code}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            text = resp.read().decode("gbk", errors="replace")
        body = text.split("=", 1)[1].strip().strip('";') if "=" in text else ""
        f = body.split("~")
        if len(f) < 35 or not f[3]:
            return {"trading": False}
        tick_date = f"{f[30][0:4]}-{f[30][4:6]}-{f[30][6:8]}"
        if tick_date != date:
            return {"trading": False}
        cur, prev, opn = float(f[3]), float(f[4]), float(f[5])
        pct = round((cur - prev) / prev * 100, 2) if prev else 0.0
        return {"trading": True, "live": True, "open": opn, "close": cur, "pct": pct,
                "high": float(f[33] or 0), "low": float(f[34] or 0),
                "vol": float(f[6] or 0)}

    def _astock_month_kline(self):
        """整月日 K：双源自愈；当月会额外把腾讯日 K 缺失的「今日形成中蜡烛」用实时行情补上。
        返回 {"days": [...], "prev_closes": [...]}；prev_closes 为月前约 40 个交易日的收盘价
        （供前端计算 MA20 均线起点），非交易月（未来月等）days 为空。"""
        import datetime
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        month = (params.get("month") or "").strip()
        if not re.match(r"^\d{4}-\d{2}$", month):
            self._send_json({"error": "invalid month"}, 400)
            return
        code = _normalize_stock_code(params.get("code") or "sh000001")
        if not code:
            self._send_json({"error": "invalid code"}, 400)
            return
        try:
            m0 = datetime.date.fromisoformat(f"{month}-01")
            ext_start = (m0 - datetime.timedelta(days=60)).isoformat()
            all_days = _fetch_kline_days(ext_start, f"{month}-31", code)
            prev_closes = [d["close"] for d in all_days if d["date"] < f"{month}-01"]
            days = [d for d in all_days if d["date"] >= f"{month}-01"]
            today = datetime.date.today().isoformat()
            if month == today[:7]:
                live = self._astock_live(today, code)
                if live.get("trading"):
                    candle = {"date": today, "open": live["open"],
                              "close": live["close"],
                              "high": live["high"] or max(live["open"], live["close"]),
                              "low": live["low"] or min(live["open"], live["close"]),
                              "vol": live["vol"]}
                    if days and days[-1]["date"] == today:
                        days[-1] = candle
                    else:
                        days.append(candle)
            self._send_json({"days": days, "prev_closes": prev_closes})
        except Exception as e:
            logger.info("A股月K获取失败 %s %s: %s", code, month, e)
            self._send_json({"error": str(e)})

    def _stock_lookup(self):
        """联网校验股票代码并补全名称（腾讯实时行情 f[1]）。
        返回 {"code": "sh600519", "name": "贵州茅台"}；代码无效返回 error。"""
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        code = _normalize_stock_code(params.get("code") or "")
        if not code:
            self._send_json({"error": "代码格式不支持（6 位数字或 sh/sz 前缀）"})
            return
        try:
            req = urllib.request.Request(
                f"https://qt.gtimg.cn/q={code}",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                text = resp.read().decode("gbk", errors="replace")
            body = text.split("=", 1)[1].strip().strip('";') if "=" in text else ""
            f = body.split("~")
            if len(f) < 2 or not f[1].strip():
                raise ValueError("not found")
            self._send_json({"code": code, "name": f[1].strip()})
        except Exception as e:
            logger.info("股票代码校验失败 %s: %s", code, e)
            self._send_json({"error": "查不到该代码，请检查是否输入正确"})

    def _save_stock(self, data):
        """自选股增/删：action = add {code, name} | del {code}，返回全量列表"""
        stocks = load_stocks()
        action = data.get("action")
        if action == "add":
            code = _normalize_stock_code(data.get("code") or "")
            name = (data.get("name") or "").strip()
            if not code or not name:
                self._send_json({"error": "代码或名称为空"})
                return
            if not any(s.get("code") == code for s in stocks):
                stocks.append({"code": code, "name": name})
        elif action == "del":
            code = (data.get("code") or "").strip().lower()
            if code == "sh000001":
                self._send_json({"error": "上证指数为内置标的，不可删除"})
                return
            stocks = [s for s in stocks if s.get("code") != code]
        else:
            self._send_json({"error": "unknown action"}, 400)
            return
        save_stocks(stocks)
        self._send_json({"stocks": stocks})

    def _save_todo(self, data):
        """待办增/改/删：action = add | toggle | del，返回全量列表"""
        import time
        todos = load_todos()
        action = data.get("action")
        if action == "add":
            text = (data.get("text") or "").strip()
            date = (data.get("date") or "").strip()
            if not text or not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
                self._send_json({"error": "待办内容或日期不合法"})
                return
            todos.append({
                "id": f"{int(time.time() * 1000)}",
                "date": date, "text": text, "done": False,
            })
        elif action == "toggle":
            tid = data.get("id")
            for t in todos:
                if t.get("id") == tid:
                    t["done"] = not t.get("done")
                    break
        elif action == "del":
            tid = data.get("id")
            todos = [t for t in todos if t.get("id") != tid]
        else:
            self._send_json({"error": "unknown action"}, 400)
            return
        save_todos(todos)
        self._send_json({"ok": True, "todos": todos})

    def _generate(self, data):
        lang = (data.pop("lang", "") or "curl").lower()
        if lang not in ("", "curl"):
            # 代码预览：按语言生成调用示例（复用 resolve_request，与执行语义一致）
            try:
                req = resolve_request(data)
                code = render_code(req, lang)
            except ValueError as e:
                self._send_json({"error": str(e)})
                return
            self._send_json({"code": code, "lang": lang})
            return
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
        self._holiday_cache = {}   # {year: {"YYYY-MM-DD": "休"/"班"}}
        self._exec_process = None
        self._exec_lock = threading.Lock()
        self._exec_mode = None     # 当前执行模式（local/ssh），供输出回调区分
        self._ssh_hold = ""        # SSH stdout 末行驻留缓冲（剥离 -w 尾注）
        self.terminal_token = secrets.token_urlsafe(32)
        self.terminals = TerminalManager()

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

        # 页面经本地 HTTP 服务加载（setHtml 有体积上限，内嵌 ECharts 后超限黑屏）
        self.view.load(QUrl(f"http://127.0.0.1:{self.port}/index.html"))

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
        # SSH 模式只驻留最后一个完整或未完成行；执行结束才能确认唯一真实尾注。
        if self._exec_mode == "ssh" and tag == "stdout":
            self._ssh_hold += text
            line_end = len(self._ssh_hold)
            if self._ssh_hold.endswith("\n"):
                line_end -= 1
            idx = self._ssh_hold.rfind("\n", 0, line_end)
            if idx >= 0:
                out, self._ssh_hold = self._ssh_hold[:idx + 1], self._ssh_hold[idx + 1:]
                if out:
                    self.push_event({"type": "output", "tag": tag, "text": out})
            return
        self.push_event({"type": "output", "tag": tag, "text": text})

    def _flush_ssh_hold(self):
        """执行结束时仅解析整个末行尾注；同形正文按原文输出，重复 flush 不发事件。"""
        hold, self._ssh_hold = self._ssh_hold, ""
        if not hold:
            return
        candidate = hold[:-1] if hold.endswith("\n") else hold
        if candidate.endswith("\r"):
            candidate = candidate[:-1]
        m = SSH_META_RE.fullmatch(candidate)
        if m:
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
                return
            except (ValueError, OverflowError):
                logger.warning("SSH 尾注解析失败: %s", m.group(0))
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

    # ──────────────────── 关闭 ────────────────────
    def closeEvent(self, event):
        if getattr(self, "_server_closed", False):
            super().closeEvent(event)
            return
        self._server_closed = True
        self.terminals.close_all()
        self._server.shutdown()
        super().closeEvent(event)
