# Dashboard 组件布局与终端面板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 启动默认展示可自定义组件网格的 Dashboard，并提供可在本地与现有 GateShell 目标机之间切换的持久命令终端。

**Architecture:** 后端用白名单规范化函数保存网格布局，用独立 `TerminalManager` 管理本地 shell 和 GateShell 交互会话；HTTP 层只负责令牌校验和路由。前端将布局编辑与终端交互分别放入 `dashboard.js`、`terminal.js`，现有 `app.js` 继续维护共享日期、请求标签和 Dashboard 数据渲染。

**Tech Stack:** Python 3 / 标准库 unittest、PyQt5 QWebEngine、ThreadingHTTPServer、Paramiko GateShell、原生 JavaScript、HTML5 Drag and Drop、CSS Grid、ECharts。

**Spec:** `docs/superpowers/specs/2026-10-24-dashboard-terminal-design.md`

## Global Constraints

- 宽度只允许 `4、6、8、12`，布局持久化到 `~/.cURL/ui_prefs.json`，不得以 localStorage 作为主要存储。
- 组件 ID 固定为 `stats、calendar、almanac、market、todo、terminal`；日历和行情/K 线共享现有 `dash.sel` 与月份状态。
- 不引入新的前端布局依赖；首版终端不支持 vim、top 等全屏 TTY 程序。
- SSH 必须复用 GateShell 菜单会话；`jump_pass` 只在后端读取，不进入 API 响应或日志。
- 所有 `/api/terminal/*` 路由校验随机令牌，HTTP 服务仅绑定 `127.0.0.1`。
- 保留用户真实主题、默认股票和 SSH 配置；测试不得改写这些文件。
- 每个任务完成后运行对应测试并提交；最终更新 README、重建 exe、实测并推送。

---

### Task 1: 布局偏好规范化与持久化

**Files:**
- Modify: `curlgen/config.py`（UI 偏好区域）
- Modify: `curlgen/web_ui.py:277-321`
- Test: `tests/test_dashboard_layout.py`

**Interfaces:**
- Produces: `normalize_dashboard_layout(value) -> {"version": 1, "items": list}`
- Produces: `DEFAULT_DASHBOARD_LAYOUT` 和 `DASHBOARD_COMPONENT_IDS`
- Consumes: 现有 `load_ui_prefs()`、`save_ui_prefs()` 与 `_UI_PREFS_LOCK`

- [ ] **Step 1: 写失败测试**

```python
import unittest
from curlgen.config import DEFAULT_DASHBOARD_LAYOUT, normalize_dashboard_layout

class DashboardLayoutTests(unittest.TestCase):
    def test_invalid_value_returns_independent_default(self):
        first = normalize_dashboard_layout(None)
        self.assertEqual(first, DEFAULT_DASHBOARD_LAYOUT)
        self.assertIsNot(first, DEFAULT_DASHBOARD_LAYOUT)
        first["items"][0]["width"] = 4
        self.assertNotEqual(first, DEFAULT_DASHBOARD_LAYOUT)

    def test_sanitizes_deduplicates_and_fills_missing_components(self):
        raw = {"version": 99, "items": [
            {"id": "calendar", "visible": False, "width": 7},
            {"id": "calendar", "visible": True, "width": 12},
            {"id": "unknown", "visible": True, "width": 12},
            {"id": "terminal", "visible": True, "width": 8},
        ]}
        result = normalize_dashboard_layout(raw)
        self.assertEqual(result["version"], 1)
        self.assertEqual([x["id"] for x in result["items"][:2]], ["calendar", "terminal"])
        self.assertEqual(result["items"][0], {"id": "calendar", "visible": False, "width": 4})
        self.assertEqual(len(result["items"]), 6)
        self.assertIn("market", [x["id"] for x in result["items"]])

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m unittest tests.test_dashboard_layout -v`
Expected: 导入 `DEFAULT_DASHBOARD_LAYOUT` 失败。

- [ ] **Step 3: 实现纯函数规范化**

```python
DASHBOARD_COMPONENT_IDS = ("stats", "calendar", "almanac", "market", "todo", "terminal")
DEFAULT_DASHBOARD_LAYOUT = {
    "version": 1,
    "items": [
        {"id": "stats", "visible": True, "width": 12},
        {"id": "calendar", "visible": True, "width": 8},
        {"id": "almanac", "visible": True, "width": 4},
        {"id": "market", "visible": True, "width": 8},
        {"id": "todo", "visible": True, "width": 4},
        {"id": "terminal", "visible": True, "width": 12},
    ],
}

def normalize_dashboard_layout(value):
    import copy
    source = value.get("items", []) if isinstance(value, dict) else []
    by_id = {item["id"]: copy.deepcopy(item) for item in DEFAULT_DASHBOARD_LAYOUT["items"]}
    result, seen = [], set()
    for item in source:
        if not isinstance(item, dict):
            continue
        component_id = item.get("id")
        if component_id not in by_id or component_id in seen:
            continue
        seen.add(component_id)
        result.append({
            "id": component_id,
            "visible": item.get("visible") is not False,
            "width": item.get("width") if item.get("width") in (4, 6, 8, 12) else by_id[component_id]["width"],
        })
    for item in DEFAULT_DASHBOARD_LAYOUT["items"]:
        if item["id"] not in seen:
            result.append(copy.deepcopy(item))
    return {"version": 1, "items": result}
```

在 `/api/ui_prefs` 中保留 `theme/stock` 字符串合并，并增加：

```python
if "dashboard" in data:
    prefs["dashboard"] = normalize_dashboard_layout(data["dashboard"])
```

从 `config.py` 导入 `normalize_dashboard_layout`。

- [ ] **Step 4: 运行测试并提交**

Run: `python -m unittest tests.test_dashboard_layout -v`
Expected: 2 个测试通过。

```bash
git add curlgen/config.py curlgen/web_ui.py tests/test_dashboard_layout.py
git commit -m "新增 Dashboard 布局偏好规范化与持久化"
```

### Task 2: 启动默认 Dashboard 与可编辑组件网格

**Files:**
- Create: `curlgen/web/dashboard.js`
- Modify: `curlgen/web/index.html:208-254,428-434`
- Modify: `curlgen/web/app.js:1478-1506,1869-1881,2681-2953,2991-3026`
- Modify: `curlgen/web/app.css:1309-1482`
- Test: `tests/browser_dashboard_checks.js`（CDP/浏览器执行断言）

**Interfaces:**
- Consumes: `/api/state` 的 `ui.dashboard` 和 `POST /api/ui_prefs {dashboard}`
- Produces: `DashboardLayout` 全局类，方法 `mount()`、`apply()`、`setEditing(bool)`、`add(id)`、`reset()`、`save()`
- Consumes: 现有 `state`、`dash`、`api()`、`renderCalendar()`、`renderAlmanac()`、`renderTodos()`、`loadAstock()`、`loadKline()`

- [ ] **Step 1: 写浏览器失败断言**

```javascript
(() => {
  const assert = (ok, message) => { if (!ok) throw new Error(message); };
  assert(!document.querySelector("#dashboard").classList.contains("hidden"), "启动应显示 Dashboard");
  assert(document.querySelectorAll(".request-tab").length === 0, "启动不应自动新建请求");
  assert(document.querySelector('[data-component="market"]'), "行情/K线应为独立组件");
  assert(document.querySelector('[data-component="terminal"]'), "应存在终端组件");
  assert(document.querySelector("#dash-edit"), "应存在布局编辑按钮");
  return {ok: true};
})()
```

Run: 用本地 HTTP 页面和 Chrome DevTools 执行；Expected: 当前固定布局缺少组件与编辑入口，断言失败。

- [ ] **Step 2: 将固定卡片改为组件容器**

在 `.dash-grid` 中为六个组件设置 `data-component` 和公共卡片结构；把 `#cal-stock`、`#cal-kline-head`、`#cal-kline` 移到 `market` 卡片。头部加入 `#dash-edit` 和 `#dash-reset`，组件编辑工具包含拖动手柄、宽度按钮和隐藏按钮。`terminal` 卡片先放置 Task 6 使用的固定 ID：

```html
<div class="dash-card terminal-card" data-component="terminal">
  <div class="dash-card-title">终端 <span id="terminal-status" class="terminal-status">未连接</span></div>
  <div id="terminal-output" class="terminal-output" aria-live="polite"></div>
  <div class="terminal-input-row">
    <span id="terminal-prompt">本地&gt;</span>
    <input id="terminal-input" autocomplete="off" spellcheck="false" placeholder="输入命令，Enter 执行">
  </div>
  <div class="terminal-toolbar">
    <button id="terminal-ssh" title="登录 SSH">SSH</button>
    <button id="terminal-local" title="切回本地">本地</button>
    <button id="terminal-interrupt">中断</button>
    <button id="terminal-reconnect">重连</button>
    <button id="terminal-clear">清屏</button>
  </div>
</div>
```

在 `app.js` 前加载 `dashboard.js`，在 `app.js` 后加载 `terminal.js`，并添加 `window.TERMINAL_TOKEN = "__TERMINAL_TOKEN__";`。

- [ ] **Step 3: 实现布局类**

```javascript
class DashboardLayout {
  constructor() { this.items = []; this.editing = false; this.dragId = null; }
  mount(layout) {
    this.items = layout.items.map(x => ({...x}));
    this.apply();
    document.querySelector("#dash-edit").onclick = () => this.setEditing(!this.editing);
    document.querySelector("#dash-reset").onclick = () => this.reset();
    this.bindCards();
  }
  apply() {
    const grid = document.querySelector(".dash-grid");
    this.items.forEach(item => {
      const card = grid.querySelector(`[data-component="${item.id}"]`);
      card.style.order = this.items.indexOf(item);
      card.style.gridColumn = `span ${item.width}`;
      card.classList.toggle("component-hidden", !item.visible);
    });
  }
  save() { return api("/api/ui_prefs", {dashboard: {version: 1, items: this.items}}); }
  setEditing(value) {
    this.editing = value;
    document.querySelector("#dashboard").classList.toggle("layout-editing", value);
  }
  add(id) {
    const item = this.items.find(x => x.id === id);
    if (item) { item.visible = true; this.apply(); this.save(); }
  }
  reset() {
    this.items = ["stats", "calendar", "almanac", "market", "todo", "terminal"].map(id => {
      const old = this.items.find(x => x.id === id);
      return {id, visible: true, width: old.width};
    });
    this.apply();
    return this.save();
  }
  bindCards() {
    document.querySelectorAll(".dash-grid [data-component]").forEach(card => {
      card.querySelector(".dash-drag").ondragstart = () => { this.dragId = card.dataset.component; };
      card.ondragover = event => { if (this.editing) event.preventDefault(); };
      card.ondrop = event => {
        event.preventDefault();
        const from = this.items.findIndex(x => x.id === this.dragId);
        const to = this.items.findIndex(x => x.id === card.dataset.component);
        if (from < 0 || to < 0 || from === to) return;
        const [moved] = this.items.splice(from, 1);
        this.items.splice(to, 0, moved);
        this.apply();
        this.save();
      };
      card.querySelector(".dash-hide").onclick = () => {
        this.items.find(x => x.id === card.dataset.component).visible = false;
        this.apply();
        this.save();
      };
      card.querySelectorAll("[data-width]").forEach(button => {
        button.onclick = () => {
          this.items.find(x => x.id === card.dataset.component).width = Number(button.dataset.width);
          this.apply();
          this.save();
        };
      });
    });
  }
}
window.DashboardLayout = DashboardLayout;
```

不允许把卡片移动到网格之外。编辑模式的组件添加入口列出当前隐藏组件；普通模式隐藏全部编辑控件。空待办显示“当日暂无待办”，不得再通过 `hidden` 移除整个组件。

- [ ] **Step 4: 接入启动和共享渲染**

删除 `init()` 中启动时的 `newTab()`。取得 `/api/state` 后执行：

```javascript
state.dashboard = normalize_dashboard_layout(j.ui && j.ui.dashboard);
dashboardLayout = new DashboardLayout();
dashboardLayout.mount(state.dashboard);
syncDashboard();
```

`normalize_dashboard_layout` 是前端同名函数，按 Task 1 的相同 ID/宽度规则校验。`renderDashboard()` 只刷新可见组件；行情/K 线请求只在 `market` 可见时发起，待办和黄历仍共享 `dash.sel`。关闭最后标签仍调用现有 `syncDashboard()`。

- [ ] **Step 5: 完成响应式样式与浏览器验证**

CSS 使用 `grid-template-columns: repeat(12, minmax(0, 1fr))`；窗口不足 1100px 时所有组件强制 `span 12`。编辑模式仅显示虚线轮廓和工具，不改变普通模式间距。浏览器断言补充：拖放后顺序改变、四种宽度生效、隐藏后可添加、刷新后布局保持、恢复默认生效、日历和 K 线点击仍更新同一日期。

Run: `python -m unittest tests.test_dashboard_layout -v` 和浏览器断言。
Expected: 全部通过，无 JavaScript 控制台错误。

- [ ] **Step 6: 提交**

```bash
git add curlgen/web/index.html curlgen/web/app.js curlgen/web/app.css curlgen/web/dashboard.js tests/browser_dashboard_checks.js
git commit -m "实现默认 Dashboard 与可编辑组件网格"
```

### Task 3: 本地持久终端会话

**Files:**
- Create: `curlgen/terminal_manager.py`
- Test: `tests/test_terminal_local.py`

**Interfaces:**
- Produces: `TerminalManager.open(kind="local", label=None) -> dict`
- Produces: `write(session_id, text)`、`events(session_id, since=0) -> dict`、`interrupt(session_id)`、`close(session_id)`、`cleanup_idle(max_age=1800)`、`close_all()`
- Produces: 会话信息 `{id, kind, label, status}`；事件 `{seq, type, text}`，状态事件文本为 `ready/connecting/closed/error`
- Consumes: 标准库 `subprocess`、`threading`、`locale`、`os`、`uuid`

- [ ] **Step 1: 写本地会话失败测试**

```python
import os, tempfile, time, unittest
from curlgen.terminal_manager import TerminalManager

def wait_text(manager, session_id, needle, timeout=5):
    deadline = time.time() + timeout
    text, since = "", 0
    while time.time() < deadline:
        result = manager.events(session_id, since)
        since = result["next"]
        text += "".join(e.get("text", "") for e in result["events"])
        if needle in text:
            return text
        time.sleep(0.05)
    raise AssertionError(f"未等到 {needle!r}，输出为 {text!r}")

class LocalTerminalTests(unittest.TestCase):
    def setUp(self): self.manager = TerminalManager()
    def tearDown(self): self.manager.close_all()

    def test_shell_preserves_working_directory_and_cursor(self):
        session = self.manager.open("local")
        sid = session["id"]
        with tempfile.TemporaryDirectory() as folder:
            self.manager.write(sid, f'cd /d "{folder}"' if os.name == "nt" else f'cd "{folder}"')
            self.manager.write(sid, "cd" if os.name == "nt" else "pwd")
            wait_text(self.manager, sid, os.path.basename(folder))
        first = self.manager.events(sid, 0)
        second = self.manager.events(sid, first["next"])
        self.assertEqual(second["events"], [])

    def test_unknown_session_and_close(self):
        with self.assertRaises(KeyError): self.manager.write("missing", "echo no")
        session = self.manager.open("local")
        self.manager.close(session["id"])
        self.assertEqual(self.manager.events(session["id"], 0)["session"]["status"], "closed")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行并确认失败**

Run: `python -m unittest tests.test_terminal_local -v`
Expected: `curlgen.terminal_manager` 不存在。

- [ ] **Step 3: 实现本地 shell 与有界事件缓冲**

```python
class TerminalManager:
    def __init__(self, session_factory=None, ssh_config_loader=None):
        self._sessions, self._lock = {}, threading.RLock()
        self._session_factory = session_factory
        self._ssh_config_loader = ssh_config_loader

    def open(self, kind="local", label=None):
        if kind not in ("local", "ssh"):
            raise ValueError("unsupported terminal kind")
        sid = uuid.uuid4().hex
        session = {"id": sid, "kind": kind, "label": label or "", "status": "connecting",
                   "events": [], "next": 1, "process": None, "adapter": None,
                   "closed": False, "last_used": time.monotonic()}
        with self._lock: self._sessions[sid] = session
        if kind == "local": self._start_local(session)
        else: threading.Thread(target=self._start_ssh, args=(session,), daemon=True).start()
        return self._info(session)
```

`_start_local` 在 Windows 使用 `[os.environ.get("COMSPEC", "cmd.exe"), "/Q"]`，其他系统使用 `["/bin/sh"]`，配置 `stdin/stdout/stderr=PIPE`、`bufsize=0`。两个读取线程分别调用 `_emit(session, "stdout"/"stderr", decoded_text)`；Windows 使用 `locale.getpreferredencoding(False)`，其他系统用 UTF-8，均 `errors="replace"`。进程退出后只发送一次 `closed`。

`_emit` 在锁内追加 `{seq, type, text}` 并递增 `next`，最多保留 1000 条且总文本最多 512KB。`events` 只返回 `seq > since` 的副本；若调用方游标落后于缓冲起点，响应增加 `"reset": True`。`write` 自动补换行，本地编码与读取编码一致；`interrupt` 本地终止进程，远端写入 `\x03`；`close` 幂等并关闭管道/适配器。

```python
def cleanup_idle(self, max_age=1800):
    now = time.monotonic()
    with self._lock:
        expired = [sid for sid, session in self._sessions.items()
                   if session["closed"] and now - session["last_used"] > max_age]
    for sid in expired:
        with self._lock:
            self._sessions.pop(sid, None)
```

`open()` 和 `events()` 入口调用 `cleanup_idle()`；活跃会话即使空闲也不得断开，已关闭会话的输出至少保留 30 分钟。每次 `events/write` 更新 `last_used`。

- [ ] **Step 4: 运行本地测试并提交**

Run: `python -m unittest tests.test_terminal_local -v`
Expected: 全部通过；重复执行无残留进程。

```bash
git add curlgen/terminal_manager.py tests/test_terminal_local.py
git commit -m "新增本地持久终端会话管理"
```

### Task 4: GateShell 交互终端适配

**Files:**
- Modify: `curlgen/ssh_executor.py:92-416`
- Modify: `curlgen/terminal_manager.py`
- Modify: `curlgen/config.py:200-225`（仅在需要严格标签校验辅助函数时）
- Test: `tests/test_terminal_ssh.py`

**Interfaces:**
- Consumes: `resolve_ssh_cfg(load_ssh_config(), label)`、`load_ssh_targets()`
- Produces: `GateShellSession.send(text: str)`、`recv_available() -> bytes`、`closed -> bool`
- Consumes: Task 3 `TerminalManager` 会话和事件接口

- [ ] **Step 1: 用假 GateShell 写失败测试**

```python
import unittest
from curlgen.terminal_manager import TerminalManager
from tests.test_terminal_local import wait_text

class FakeGate:
    def __init__(self, cfg, on_log=None, stop_event=None):
        self.cfg, self.sent, self.closed = cfg, [], False
    def open(self): pass
    def connect_host(self, target):
        assert target == self.cfg["target_host"]
    def send(self, text): self.sent.append(text)
    def recv_available(self): return b"root@test\n" if self.sent else b""
    @property
    def closed(self): return self.closed
    def close(self): self.closed = True

class SSHTerminalTests(unittest.TestCase):
    def test_connects_selected_target_without_exposing_password(self):
        cfg = {"jump_host": "gate", "jump_user": "u", "jump_pass": "secret",
               "target_host": "10.0.0.8", "target_label": "生产A"}
        manager = TerminalManager(session_factory=FakeGate, ssh_config_loader=lambda: cfg)
        session = manager.open("ssh", "生产A")
        wait_text(manager, session["id"], "root@test")
        manager.write(session["id"], "hostname")
        self.assertIn("secret", cfg["jump_pass"])
        self.assertNotIn("secret", str(manager.events(session["id"], 0)))
        manager.close_all()

    def test_rejects_unknown_target(self):
        manager = TerminalManager(session_factory=FakeGate, ssh_config_loader=lambda: {
            "target_host": "10.0.0.8", "target_label": "生产A"})
        session = manager.open("ssh", "不存在")
        wait_text(manager, session["id"], "目标机")
        self.assertEqual(manager.events(session["id"], 0)["session"]["status"], "error")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行并确认失败**

Run: `python -m unittest tests.test_terminal_ssh -v`
Expected: `GateShellSession.send/recv_available` 或 SSH 管理逻辑缺失。

- [ ] **Step 3: 增加最小公共通道封装**

```python
def send(self, text):
    if self._chan is None or self._chan.closed:
        raise ExecutionError("SSH 会话已断开")
    data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    self._chan.send(data)

def recv_available(self):
    if self._chan is None or self._chan.closed or not self._chan.recv_ready():
        return b""
    return self._chan.recv(65536)

@property
def closed(self):
    return self._chan is None or self._chan.closed
```

`TerminalManager._start_ssh` 必须严格检查 `label == cfg.target_label`，不匹配时发送 `error: 目标机配置不存在`，不得静默连接第一台。有效时创建 `GateShellSession(cfg["jump_host"], int(cfg.get("jump_port") or 22), cfg["jump_user"], cfg["jump_pass"])`，`open()` 后 `connect_host(cfg["target_host"])`，发 `ready` 事件，再循环 `recv_available()`；无数据时 `sleep(0.05)`，通道关闭时发 `closed`。异常只发送 `str(e)`，不得发送配置字典。

- [ ] **Step 4: 运行两类终端测试并提交**

Run: `python -m unittest tests.test_terminal_local tests.test_terminal_ssh -v`
Expected: 全部通过，现有 `SSHRemoteExecutor` 行为不变。

```bash
git add curlgen/ssh_executor.py curlgen/terminal_manager.py tests/test_terminal_ssh.py
git commit -m "接入 GateShell 持久交互终端"
```

### Task 5: 终端 API、随机令牌与退出清理

**Files:**
- Modify: `curlgen/web_ui.py:8-44,161-190,214-338,651-689,868-875`
- Modify: `curlgen/web/index.html:428-434`
- Test: `tests/test_terminal_api.py`

**Interfaces:**
- Consumes: Task 3/4 `TerminalManager` 全部方法
- Produces: `POST /api/terminal/open`、`POST /api/terminal/input`、`POST /api/terminal/interrupt`、`POST /api/terminal/close`、`GET /api/terminal/events?id=...&since=N`
- Produces: `window.TERMINAL_TOKEN`（由静态页面替换）

- [ ] **Step 1: 写令牌与路由失败测试**

使用标准库 `unittest` 和 fake manager/handler，覆盖令牌和响应脱敏：

```python
import unittest
from curlgen.web_ui import _ApiHandler, terminal_token_valid

class FakeTerminals:
    def open(self, kind="local", label=None):
        return {"id": "t1", "kind": kind, "label": label, "status": "connecting"}

class FakeApp:
    terminals = FakeTerminals()

class TerminalAPITests(unittest.TestCase):
    def test_rejects_missing_or_wrong_token(self):
        self.assertFalse(terminal_token_valid("expected", None))
        self.assertFalse(terminal_token_valid("expected", "wrong"))
        self.assertTrue(terminal_token_valid("expected", "expected"))

    def test_open_response_contains_only_public_session_fields(self):
        handler = _ApiHandler.__new__(_ApiHandler)
        handler.app = FakeApp()
        response = handler._terminal_open({"kind": "ssh", "label": "生产A"})
        self.assertEqual(response, {"id": "t1", "kind": "ssh", "label": "生产A", "status": "connecting"})
        self.assertNotIn("secret", str(response))
        self.assertNotIn("jump_pass", str(response))

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行并确认失败**

Run: `python -m unittest tests.test_terminal_api -v`
Expected: `terminal_token_valid` 和终端路由不存在。

- [ ] **Step 3: 实现令牌和路由**

```python
import hmac, secrets

def terminal_token_valid(expected, supplied):
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))

class _ApiHandler(BaseHTTPRequestHandler):
    def _terminal_open(self, payload):
        return self.app.terminals.open(
            kind=payload.get("kind") or "local",
            label=payload.get("label") or None,
        )
```

实际文件保留现有 `_ApiHandler` 类，只加入 `_terminal_open` 方法，不重复定义类。`WebCurlGenerator.__init__` 创建 `self.terminal_token = secrets.token_urlsafe(32)` 与 `self.terminals = TerminalManager()`。`_serve_static` 对 index 同时替换 `__PORT__` 和 `__TERMINAL_TOKEN__`。所有终端路由在读取业务参数前校验 `X-CurlGen-Token`，失败返回 403：

```python
if path.startswith("/api/terminal/") and not terminal_token_valid(
        self.app.terminal_token, self.headers.get("X-CurlGen-Token")):
    self._send_json({"error": "forbidden"}, 403)
    return
```

路由只传递 `kind/label/id/text/since`；`since` 必须是非负整数，非法值返回 400。`closeEvent` 在 shutdown 前调用 `self.terminals.close_all()`。服务器保持 `("127.0.0.1", 0)`，不增加跨域 OPTIONS。

- [ ] **Step 4: 运行后端全测并提交**

Run: `python -m unittest discover -s tests -p "test_*.py" -v`
Expected: 全部通过；静态 HTML 不含真实密码，错误令牌请求为 403。

```bash
git add curlgen/web_ui.py curlgen/web/index.html tests/test_terminal_api.py
git commit -m "增加受令牌保护的终端 API"
```

### Task 6: 终端面板交互与服务器切换

**Files:**
- Create: `curlgen/web/terminal.js`
- Modify: `curlgen/web/index.html`（终端卡片和脚本顺序）
- Modify: `curlgen/web/app.js`（Dashboard 初始化/可见性钩子）
- Modify: `curlgen/web/app.css`（终端样式）
- Test: `tests/browser_terminal_checks.js`

**Interfaces:**
- Consumes: Task 5 终端 API、`window.TERMINAL_TOKEN`、`state.ssh.targets`、`showCtxMenu()`、`toast()`
- Produces: `TerminalPanel` 全局类，方法 `ensureLocal()`、`openLocal()`、`openSSH(label)`、`poll()`、`send()`、`interrupt()`、`close()`、`clear()`

- [ ] **Step 1: 写浏览器失败断言**

```javascript
(async () => {
  const assert = (ok, message) => { if (!ok) throw new Error(message); };
  assert(window.terminalPanel, "TerminalPanel 应初始化");
  await window.terminalPanel.openLocal();
  const input = document.querySelector("#terminal-input");
  input.value = "echo CURLGEN_TERMINAL_OK";
  await window.terminalPanel.send();
  await new Promise(resolve => setTimeout(resolve, 700));
  assert(document.querySelector("#terminal-output").textContent.includes("CURLGEN_TERMINAL_OK"), "应显示本地命令输出");
  document.querySelector("#terminal-ssh").click();
  assert(document.querySelector("#ctx-menu"), "SSH 按钮应打开服务器菜单");
  return {ok: true};
})()
```

Expected: 当前页面没有 `terminalPanel`，断言失败。

- [ ] **Step 2: 实现终端 API 客户端**

```javascript
async function terminalApi(path, body) {
  const options = {headers: {"X-CurlGen-Token": window.TERMINAL_TOKEN}};
  if (body !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "text/plain";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(window.API_BASE + path, options);
  const json = await response.json();
  if (!response.ok || json.error) throw new Error(json.error || `HTTP ${response.status}`);
  return json;
}
```

`TerminalPanel` 保存 `id/kind/label/cursor/history/historyIndex/polling`。所有轮询用 `setTimeout` 串行调度，只有前一次结束后才发起下一次；组件隐藏或请求工作台时降低频率，页面回到 Dashboard 后恢复。输出用 `document.createTextNode` 追加，不能 `innerHTML` 注入；`reset` 时清空并重放本次事件。

- [ ] **Step 3: 实现命令、历史与连接切换**

Enter 调用 `send()`，上下箭头遍历历史；`openLocal/openSSH` 先关闭旧会话再打开新会话，并清空输出和游标。SSH 按钮从 `state.ssh.targets || []` 生成 `label(host)` 菜单；无配置时 toast“请先在 SSH 设置中配置堡垒机和目标机”。状态统一显示“本地 / 连接中 / 标签 / 已断开 / 错误”。中断调用 `/interrupt`，重连按当前 kind/label 重开，清屏只清前端输出。

Dashboard 可见且 `terminal` 组件可见时仅调用一次 `ensureLocal()`；布局隐藏终端不自动杀死会话，切回本地按钮显式切换。打开请求工作台不必断开会话。

- [ ] **Step 4: 完成样式和真实浏览器验证**

终端输出使用等宽字体、深色半透明背景、最小高度 220px、自动滚到底部；用户手动向上滚动时不强制拉回。工具栏保持单行紧凑，输入框宽度不被按钮挤压。运行 `tests/browser_terminal_checks.js`，并实际验证 `cd` 后 `cd/pwd`、上下历史、清屏、错误命令输出、中断/重连、SSH 菜单与无配置提示。

Run: `node --check curlgen/web/terminal.js`（Node 可用时）及浏览器断言。
Expected: 全部通过，无控制台错误和重复输出。

- [ ] **Step 5: 提交**

```bash
git add curlgen/web/terminal.js curlgen/web/index.html curlgen/web/app.js curlgen/web/app.css tests/browser_terminal_checks.js
git commit -m "实现本地与 SSH 可切换终端面板"
```

### Task 7: 回归、文档、打包与交付

**Files:**
- Modify: `README.md`
- Modify: `CurlGenerator.spec`（仅当新增资源未被 `curlgen/web` 整体包含时）
- Verify: 上述全部源码和测试

**Interfaces:**
- Consumes: Tasks 1-6 的全部公开接口
- Produces: 更新后的说明文档和 `dist/CurlGenerator.exe`

- [ ] **Step 1: 更新 README**

在 Dashboard 章节记录六类组件、拖动排序、四种宽度、显隐、恢复默认、启动行为和 `ui_prefs.json`；在终端章节记录本地/GateShell 会话、服务器选择、历史/中断/重连及非全屏 TTY 限制；在 API 表加入五个终端路由并注明令牌保护。不得记录真实令牌、密码或用户配置。

- [ ] **Step 2: 运行静态检查和自动化测试**

```bash
python -m py_compile curlgen/config.py curlgen/terminal_manager.py curlgen/ssh_executor.py curlgen/web_ui.py
python -m unittest discover -s tests -p "test_*.py" -v
node --check curlgen/web/app.js
node --check curlgen/web/dashboard.js
node --check curlgen/web/terminal.js
git diff --check
```

Expected: 所有命令退出码为 0。Node 不在 PATH 时使用 Qoder 工作区 Node，不在系统内静默安装依赖。

- [ ] **Step 3: 浏览器完整回归**

验证启动 Dashboard、六组件布局编辑与刷新恢复、空待办、日期/黄历/待办/行情/K 线联动、主题与默认股票不变、新建/切换/关闭请求标签、本地 HTTP 执行、原 SSH 请求执行、终端本地与 SSH 命令。SSH 实测执行 `hostname` 和 `pwd`，确认选择的就是目标标签且输出无密码。

- [ ] **Step 4: 重建 exe 并用 CDP 实测**

```bash
taskkill //F //IM CurlGenerator.exe
python -m PyInstaller CurlGenerator.spec --noconfirm --clean
```

若进程不存在则继续，其他构建错误必须修复。以 `QTWEBENGINE_REMOTE_DEBUGGING=9224` 启动新 exe，读取 `http://127.0.0.1:9224/json` 并通过 CDP 执行 Task 2/6 的核心断言；检查终端真实输出和布局文件恢复，最后恢复测试前的用户偏好。

- [ ] **Step 5: 最终提交与推送**

```bash
git add README.md CurlGenerator.spec tests
git commit -m "完善 Dashboard 终端文档与验证"
git push origin master
git status --short
```

Expected: 工作树干净，远端推送成功；最终报告测试、SSH 实测、exe 验证结果和任何未完成限制。
