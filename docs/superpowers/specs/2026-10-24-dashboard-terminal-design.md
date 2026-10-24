# Dashboard 组件布局与终端面板设计

日期：2026-10-24
状态：已确认方案 A（可排序组件网格）

## 目标与范围

- 应用启动默认显示 Dashboard，不再自动创建请求标签；关闭全部请求标签后仍返回 Dashboard。
- Dashboard 支持统计、日历、黄历、股票行情与 K 线、待办、终端组件的排序、宽度调整和显隐。
- 终端默认使用本地持久 shell，并可通过现有 GateShell 配置选择目标机、登录并执行命令。
- 首版终端不支持 vim、top 等全屏 TTY 程序，不引入新的前端布局依赖。

## 首页组件与布局

采用原生 HTML5 拖放和 CSS 12 列网格。普通模式保持内容紧凑；编辑模式显示拖拽手柄、宽度切换、隐藏和组件添加入口，并提供恢复默认布局。

组件包括 `stats`、`calendar`、`almanac`、`market`、`todo`、`terminal`。股票行情/K 线从日历卡片拆出，但与日历共享 `dash.sel` 和 `dash.year/month`，保持日期与蜡烛点击联动。组件渲染复用现有日历、黄历、行情、K 线和待办逻辑，避免重复请求。

布局保存到 `~/.cURL/ui_prefs.json` 的 `dashboard` 键，结构为：

```json
{
  "version": 1,
  "items": [
    {"id": "calendar", "visible": true, "width": 8}
  ]
}
```

`width` 仅允许 4、6、8、12，分别对应 1/3、1/2、2/3 和整行；数组顺序即组件顺序。后端按组件 ID 白名单校验、去重并补齐缺失组件，前端对旧偏好回退默认布局。主题、默认股票等已有键保持不变，写入继续使用现有锁。

## 终端架构

新增独立终端会话管理模块，统一封装本地 shell 与 GateShell：

- 本地：Windows 使用 `cmd.exe /Q`，其他平台使用 `/bin/sh`；stdin/stdout/stderr 使用管道和后台读取线程，保持当前目录及 shell 环境。
- SSH：复用 `GateShellSession.open()`、`connect_host()` 和交互通道；按目标机标签调用 `resolve_ssh_cfg()`，密码只在后端读取。
- 会话：随机 ID、类型、目标标签、状态、单调递增输出序号和有界缓冲区；支持打开、写入、轮询、中断、关闭和全部清理。应用退出时关闭全部会话，空闲会话定期回收。
- 前端：输出区、命令输入、历史命令、清屏、中断/断开、重连、本地/SSH 状态和底部 SSH 服务器选择按钮。SSH 未配置时引导到现有 SSH 设置。
- 会话不持久化；重启只恢复布局，终端回到默认本地模式。

所有终端接口均校验应用启动时生成的随机令牌，服务继续仅绑定 `127.0.0.1`。日志和 API 响应不得泄露 `jump_pass`。输出按序增量拉取，避免重复渲染；断线、认证失败、目标机不存在和本地进程退出均显示可操作错误。

建议接口：

| 方法 | 路由 | 用途 |
| --- | --- | --- |
| `POST` | `/api/terminal/open` | 打开本地或指定 SSH 目标会话 |
| `POST` | `/api/terminal/input` | 写入命令或控制字符 |
| `GET` | `/api/terminal/events?id=...&since=N` | 增量输出与状态 |
| `POST` | `/api/terminal/close` | 关闭会话 |

## 修改边界

- `curlgen/terminal_manager.py`：新增终端会话管理。
- `curlgen/ssh_executor.py`：仅增加交互通道读写所需的公共封装，保留现有请求执行行为。
- `curlgen/web_ui.py`：终端路由、令牌校验、退出清理和布局偏好白名单。
- `curlgen/web/index.html`、`app.js`、`app.css`：组件网格、编辑模式、终端面板和启动逻辑。
- `README.md`：使用说明、持久化与接口文档。

## 验证

1. 单元/接口测试：布局校验与合并、本地 `cd` 后目录保持、输出增量、关闭清理、非法令牌拒绝。
2. 前端实测：启动 Dashboard、拖拽排序、宽度与显隐、重启恢复、恢复默认、日期/K 线联动、终端历史与状态。
3. SSH 实测：读取配置服务器列表，选择目标机并执行 `hostname`、`pwd`；认证或连接失败时不泄露密码。
4. 回归：新建/关闭请求标签、原本地 HTTP 与 SSH 请求执行、主题和默认股票恢复。
5. 更新 README，重建 `dist/CurlGenerator.exe`，通过 CDP 检查实际打包页面和关键行为。
