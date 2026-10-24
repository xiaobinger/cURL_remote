# Task 7 第 1 轮修复报告

## 范围与结论

- 仅处理 `task-7-review.md` 的 Important 1 与 Minor 1；未修改 SDD 状态，未 merge/push，未派发子代理。
- 修复提交：`faa5f4b`（`修复 SSH 尾注解析与文档说明`）。本报告单独提交。
- `curlgen/web_ui.py` 的 SSH 输出解析不再因任意 `search()` 命中提前提交 `http_resp`；正文、同形标记和真实尾注按完整事件契约处理。
- README 已统一空待办行为，并在 `/api/ui_prefs` 行补充 `dashboard` 六组件顺序、显隐、列宽。
- worktree `dist/CurlGenerator.exe` 已从修复源码重新构建并完成真实 exe/CDP 复验。

## RED 证据

先在 `tests/test_terminal_api.py` 增加 `SSHExecutionEventContractTests`，每个用例直接比较完整事件字典序列，输出正文采用精确字符串断言，而非只检查计数或 `NotIn`。

修复前执行：

```bash
python -m unittest tests.test_terminal_api.SSHExecutionEventContractTests -v
```

结果：`Ran 5 tests ... FAILED (failures=5)`；普通多行用例通过，其余 4 个测试方法共 5 个场景按预期失败：

- A：正文与真实尾注同 chunk 时，实际先 `http_resp` 后 output，且正文尾部换行被旧 `.strip("\r\n")` 删除。
- B：尾注换行独立 chunk 时，实际多出一个仅含 `\n` 的 output。
- C：正文同形标记合并/分块两个子场景均失败，出现错误的 201 响应、重复 `http_resp`、正文丢失或顺序错误。
- 最终重复 flush 用例同时锁定完整事件序列和正文换行，旧实现因 A 类顺序/正文破坏失败。
- 普通多行输出是保持原有行为的保护用例，因此不人为制造 RED。

GREEN 后补充同形正文场景的逐字符 chunk 子用例，覆盖任意小分块下的状态驻留和释放行为。

## 最小解析设计

`_on_exec_progress`：

- SSH stdout 追加到 `_ssh_hold` 后，只释放最后一个完整或未完成行之前的原文。
- 最后一行连同其可选换行继续驻留；后续正文到来时，先前的同形标记会作为普通正文按原顺序释放。
- 不再对整个 hold 搜索并立即 flush，因此执行结束前不会把正文中的同形标记误作真实尾注。

`_flush_ssh_hold`：

- 仅在执行结束路径确认尾注；只移除末尾一个 `\n` 及其可选 `\r`，再对完整末行使用 `SSH_META_RE.fullmatch()`。
- 匹配时只发一个结构化 `http_resp`，不匹配或数值解析失败时原样输出 hold，不对正文做全局替换或 `strip()`。
- 入口先清空 hold，因此重复 flush 不重复发事件；所有正文 output 均先于最终 `http_resp`。

未修改 SSH 协议、`SSH_META_FMT`、执行器、终端协议、前端消费结构或 API。

## GREEN 与静态验证

最终聚焦测试：

```bash
python -m unittest tests.test_terminal_api.SSHExecutionEventTests tests.test_terminal_api.SSHExecutionEventContractTests -v
```

结果：6/6 通过。覆盖：

- 正文与最终尾注同 chunk，output 严格先于 `http_resp`。
- 尾注后换行独立 chunk，不泄漏空 output。
- 无尾换行的真实尾注。
- 正文同形 `__CG_META__` 行后仍有正文和唯一真实尾注；合并、分行及逐字符 chunk 均精确保留正文且只生成真实 200 响应。
- 普通多行 stdout 的正文和事件顺序精确保留。
- 最终重复 flush 不重复发事件。

完整验证：

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python -m py_compile curl_generator.py curlgen/*.py tests/test_terminal_api.py
node --check curlgen/web/app.js
node --check curlgen/web/dashboard.js
node --check curlgen/web/terminal.js
node --check tests/browser_terminal_checks.js
git diff --check
```

结果：完整 unittest 62/62 通过；Python 编译、三个生产 JS 与浏览器测试 JS 语法检查、`git diff --check` 均通过。Node 使用 Qoder 工作区 Electron 的 `ELECTRON_RUN_AS_NODE=1` 模式。`git diff --check` 仅有既有的 LF/CRLF 提示，无 whitespace error。

## 重建与真实 exe 复验

构建命令：

```bash
python -m PyInstaller CurlGenerator.spec --noconfirm --clean
```

结果：PyInstaller 6.22.3 成功生成 worktree `dist/CurlGenerator.exe`；仍有既有 `Hidden import "sip" not found!` warning。

以 `QTWEBENGINE_REMOTE_DEBUGGING=9224` 启动真实 exe，通过 `http://127.0.0.1:9224/json` 和 CDP `Runtime.evaluate` 驱动真实 API/页面：

### 原 SSH 只读请求

- 请求 `https://example.com/`，使用既有原 SSH 请求执行路径；未执行或重复 GateShell `hostname`/`pwd`。
- 实测：`exitCode=0`、`mode=ssh`、结构化 `httpResp.status=200`、`done=true`。
- 响应正文可见 `Example Domain`；后端事件中正文 output 位于 `http_resp` 前。
- 展开后的 `__CG_META__:200:...` 尾注不在 output/Console 正文中。命令回显中的静态 `-w` 格式串不属于展开尾注，未误报为泄漏。

### 核心本地终端回归

- 在真实 exe 中运行 `tests/browser_terminal_checks.js`：21/21 通过。
- `maxActivePolls=2`（旧/新会话切换重叠），`maxActivePollsPerSession=1`，共观察 191 次请求。
- 覆盖真实本地 echo、cwd、历史、清屏、stderr、滚动、串行轮询、seq/reset、切换、中断/重连、关闭失败恢复及布局行为。
- 首次驱动时因前置 SSH 请求标签仍打开，Dashboard 不可见而在显隐用例失败；确认是验证前置状态问题。清空临时请求标签、回到 Dashboard 后按套件前置条件完整重跑，21/21 通过。未为该测试前置问题修改生产代码。

复验结束后关闭本地终端会话所在应用及全部 `CurlGenerator.exe` 进程。强制结束后台 exe 导致的工具侧非零退出是主动清理结果，不是应用验证失败。

## 用户偏好快照与恢复

- 在任何配置敏感测试前，将真实 `~/.cURL/` 顶层 JSON 按原字节快照至 `C:\Users\admin\AppData\Local\Temp\curlgen-task7-user-backup`，并保存 SHA256 manifest；未输出配置正文或秘密。
- 测试结束关闭 exe 后按快照恢复，并清理测试期间新增的非快照顶层 JSON。
- 逐文件 SHA256 核对：`allRestored=true`、`filesVerified=9`、`sshConfigUnchanged=true`。
- 未读取、展示或记录真实 SSH 密码、令牌或用户偏好内容。

## Commit

- `faa5f4b 修复 SSH 尾注解析与文档说明`
  - `curlgen/web_ui.py`
  - `tests/test_terminal_api.py`
  - `README.md`
- 本报告随后单独提交；未 push、未 merge。

## Concerns

- PyInstaller 继续输出既有 `sip` hidden-import warning；实际 exe 启动、SSH 和本地终端验证均通过。
- 真实 SSH/example.com 复验依赖当前网络及既有配置可用，本轮未改变 GateShell 配置或协议。
- 按本轮范围未重复真实 GateShell `hostname`/`pwd`，因为未修改终端/GateShell 路径。
