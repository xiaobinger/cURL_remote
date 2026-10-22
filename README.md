<div align="center">

<img src="logo.png" alt="cURL Generator" width="120" />

# cURL Generator · 星轨控制台

**把接口调试、签名计算、堡垒机远程执行，收进一座深空座舱。**

*Generate · Sign · Execute — 一处搞定从 cURL 拼装到远程落地的全链路*

[![Version](https://img.shields.io/badge/version-2.0.0-4c8dff.svg?style=flat-square)](#)
[![Python](https://img.shields.io/badge/Python-3.8+-3776AB.svg?style=flat-square&logo=python&logoColor=white)](#)
[![PyQt5](https://img.shields.io/badge/PyQt5-5.x-41CD52.svg?style=flat-square&logo=qt&logoColor=white)](#)
[![QtWebEngine](https://img.shields.io/badge/QtWebEngine-Chromium-22d3ee.svg?style=flat-square)](#)
[![paramiko](https://img.shields.io/badge/SSH-paramiko-a78bfa.svg?style=flat-square)](#)
[![Platform](https://img.shields.io/badge/platform-Windows-0078D6.svg?style=flat-square&logo=windows&logoColor=white)](#)
[![License](https://img.shields.io/badge/license-MIT-f7c948.svg?style=flat-square)](#)

</div>

---

## 📖 目录

- [一句话定位](#-一句话定位)
- [为什么需要它](#-为什么需要它)
- [核心特性](#-核心特性)
- [界面速览](#-界面速览)
- [架构总览](#-架构总览)
- [快速开始](#-快速开始)
- [目录结构](#-目录结构)
- [核心机制详解](#-核心机制详解)
  - [环境变量引用 `{{ref}}`](#1-环境变量引用-ref)
  - [签名计算](#2-签名计算javajava-兼容)
  - [GateShell 堡垒机远程执行](#3-gateshell-堡垒机远程执行)
  - [双 UI 与本地 API 服务](#4-双-ui-与本地-api-服务)
- [本地 HTTP API 一览](#-本地-http-api-一览)
- [配置持久化](#-配置持久化)
- [快捷键](#-快捷键)
- [打包发布](#-打包发布)
- [技术栈](#-技术栈)
- [常见问题 FAQ](#-常见问题-faq)
- [开发路线图](#-开发路线图)

---

## 🎯 一句话定位

**cURL Generator** 是一款面向 **接口联调 / 签名调试 / 远程运维** 场景的桌面工具：用图形界面拼装请求，一键生成可粘贴的 `curl` 命令；内置 **Java 风格签名算法**（MD5 / SHA256）；并通过 **GateShell 菜单式堡垒机** 把命令直接投递到内网目标机执行，输出实时回传。

> 从「手写 cURL」到「所见即所得的请求工作台」，它消灭了拼 URL、算签名、登堡垒机、贴命令这四步的重复劳动。

---

## 💡 为什么需要它

后端联调时的日常痛点，它逐一接管：

| 痛点 | cURL Generator 的解法 |
| --- | --- |
| 手拼 query / header / JSON body，格式一错就 400 | 结构化表单 + **一键美化/压缩**，自动拼装并转义 |
| 签名规则写死在文档里，每次都要手算 MD5/SHA256 | 环境里配好密钥与算法，**生成时自动算签、自动注入** |
| 不同环境（测试/预发/生产）域名、密钥来回切换 | **环境配置 + `{{变量}}` 引用**，切环境即切全量参数 |
| 内网机器不通外网，要在堡垒机上敲命令 | **堡垒机一键透传**，本地生成、远端执行、输出回显 |
| 常用请求每次重填 | **快捷请求分组** + **历史记录**，点一下即复活 |
| 工具界面丑、看久了累 | **深空座舱 / 晨雾** 双主题，深色护眼、发光主按钮 |

---

## ✨ 核心特性

<table>
<tr>
<td width="50%" valign="top">

### 🧭 请求工作台
- 方法选择（GET/POST/PUT/DELETE/PATCH/HEAD/OPTIONS），**方法联动页签**：POST 类自动切 Body、GET 类自动切 Query Params（手动切换、打开快捷请求、切换标签均生效）
- URL、Query Params、Headers 键值表编辑
- Body 支持 **JSON / Form Data / Raw Text / None**
- Body **美化 / 压缩** 一键切换
- Auth：Bearer Token / Basic Auth / API Key
- Options：`-k` 跳过 SSL、`-s` 静默、代理、超时

</td>
<td width="50%" valign="top">

### 🔐 签名与环境
- **MD5 / SHA256** 双算法，Java 风格大小写不敏感排序
- 签名参数名可自定义（默认 `sign`）
- 多环境配置（别名 / 密钥 / 算法 / 目标机）
- **`{{变量}}` 引用** + 编辑器内高亮 + 悬浮显示真实取值
- GET 系列签名自动落到 URL，其余注入 Body

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🖧 远程执行
- **本地直发**：「▶ 执行（本地）」一键发起，无弹窗打断
- **SSH 远程**：cURL 预览框右下角「🖧 SSH 请求」，二次确认后经堡垒机执行
- 本地执行：`Popen` 双线程实时流式读取
- **GateShell 堡垒机**密码认证菜单接入
- **多目标机**管理（标签 / 主机 / 端口 / 用户）
- 命令哨兵包裹，精确捕获退出码
- 超时保护、手动停止、连接测试

</td>
<td width="50%" valign="top">

### 🎨 体验与工程
- **双 UI 引擎**：星轨控制台（Web）+ 经典 PyQt，随时互切
- **7 套主题**：深空蓝 / 晨雾白 / 曜石黑 / 暗夜紫 / 墨绿森林 / 经典QQ / 阳光橙，菜单选择并持久记忆
- 快捷请求分组、历史记录、一键复制
- **多标签页**（Postman 式）：每个请求一个标签，脏标记 ●，互不惊扰
- **cURL 实时预览**：编辑即生成，无需手动点击；历史记录只记真实执行的请求
- 响应区：退出码 / 耗时 / 体积元信息，JSON Pretty / Raw 切换，一键复制
- 侧栏页签式（快捷请求 / 历史记录切换）+ 搜索 + 请求项右键（新标签打开 / 重命名 / 删除）+ 分组计数徽章；设置类入口收纳为底部图标行
- **一键导入** Postman Collection v2.1 / 环境变量 / OpenAPI 3（Apifox 可导出）：分组、鉴权、前后置脚本全映射，同名自动合并；环境变量导入为「自定义参数」，`{{变量名}}` 引用即刻生效
- KV 行启用勾选：禁用的 Header / Param 不进生成的命令
- **前后置脚本**：JavaScript 沙箱 + Postman 风格 `pm` API；**AI 智能生成**——脚本框右下角 ✨ AI 按钮唤起生成对话框，描述需求→生成预览→一键应用回填（OpenAI 兼容协议，内置通义 / DeepSeek / Kimi 预设，支持自定义中转站，Key 仅存本地）
- 按天滚动日志（保留 30 天）
- PyInstaller 单文件打包，免环境运行

</td>
</tr>
</table>

---

## 🖥 界面速览

**新版 · 星轨控制台（Aurora）** —— 由 QWebEngineView 渲染的原生 Web 界面：

```
┌──────────────┬──────────────────────────────────────────────────────────┐
│  cURL Gen    │  [GET ▾]  https://api.example.com/v1/resource   [生产 ▾]  │
│  星轨控制台   ├──────────────────────────────────────────────────────────┤
│              │  复制 保存请求 ▶执行(本地) ■停止                就绪      │
│ ⚡ 请求构建   ├───────────────────────────────┬──────────────────────────┤
│ 🌐 环境管理   │ [Headers][Params][Body][Auth] │  cURL 预览（实时生成）     │
│ 🖧 SSH 设置   │ ┌───────────────────────────┐ │  curl -X POST \           │
│ 🌓 切换主题   │ │ key        │ value        │ │    -H "Content-Type:..."  │
│ ↩ 返回旧版   │ │ X-Sign     │ {{appKey}}   │ │    -d '{...}'  [🖧SSH请求]│
│              │ └───────────────────────────┘ │                          │
│ 快捷请求 ▸    ├───────────────────────────────┼──────────────────────────┤
│  · 商户余额   │                               │  响应结果      200 OK     │
│  · 下单接口   │                               │  { "code": 0, ... }       │
│ 历史记录 ▸    │                               │                          │
│ ● 本地模式   │                               │                          │
└──────────────┴───────────────────────────────┴──────────────────────────┘
```

> 左侧导航四大区块：**请求构建 / 环境管理 / SSH 设置 / 切换主题**，底部常驻 **快捷请求** 与 **历史记录**，以及实时 **连接状态徽标**。

**旧版 · 经典 PyQt 原生界面** —— 通过 `--legacy` 或菜单栏一键切回，零 WebEngine 依赖。

---

## 🏛 架构总览

```mermaid
flowchart TB
    subgraph Entry["入口 curl_generator.py"]
        A["QApplication + Fusion 样式"]
        B{"能否 import<br/>QtWebEngineWidgets?"}
        C["--legacy ?"]
    end

    subgraph WebUI["新版 · curlgen/web_ui.py"]
        W1["QWebEngineView 渲染<br/>curlgen/web/ (HTML/CSS/JS)"]
        W2["ThreadingHTTPServer<br/>127.0.0.1:随机端口"]
        W3["事件流 /api/events<br/>执行输出回推"]
    end

    subgraph Legacy["旧版 · curlgen/main_window.py"]
        L1["纯 PyQt5 控件界面"]
    end

    subgraph Shared["共享核心"]
        S1["curl_builder.py 命令构建"]
        S2["signing.py / sign_utils.py 签名"]
        S3["ssh_executor.py 本地 & 堡垒机执行"]
        S4["config.py 配置持久化 ~/.cURL"]
        S5["theme.py 设计令牌 / QSS"]
    end

    A --> C
    C -->|是| L1
    C -->|否| B
    B -->|是| WebUI
    B -->|否| L1
    W1 <-->|fetch JSON| W2
    W2 --> S1
    L1 --> S1
    S1 --> S2
    WebUI --> S3
    L1 --> S3
    S1 --> S4
    S3 --> S4
```

**设计原则：**

- **核心与界面解耦**：命令构建、签名、执行三大逻辑都在纯 Python 模块里，双 UI 共用同一份实现，行为绝对一致。
- **新版 UI 走 Web 化路线**：把界面交给浏览器引擎，获得远超传统 Qt 控件的动效与排版自由度；Python 端只做数据与执行，通过本地 HTTP + 事件流通信。
- **降级友好**：没装 PyQtWebEngine 自动退回旧版；偏好原生界面用 `--legacy` 强制切换。

---

## 🚀 快速开始

### 环境要求

| 依赖 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.8+ | 建议 3.10 ~ 3.11 |
| PyQt5 | 5.15+ | GUI 基础（必装） |
| PyQtWebEngine | 5.15+ | 新版星轨控制台（选装，缺失自动降级） |
| paramiko | 2.x / 3.x | SSH 堡垒机远程执行（选装） |
| pyinstaller | 5+ | 仅打包时需要 |

### 安装依赖

```bash
pip install PyQt5 PyQtWebEngine paramiko
```

> 仅想用经典界面：`pip install PyQt5` 即可（不装 PyQtWebEngine 会自动使用旧版 UI）。

### 源码运行

```bash
# 默认：优先新版星轨控制台
python curl_generator.py

# 强制经典 PyQt 界面
python curl_generator.py --legacy
```

### 直接使用发行版

下载 `dist/CurlGenerator.exe`，双击即用 —— 无需安装 Python 环境。

---

## 📁 目录结构

```text
buildCurl/
├── curl_generator.py          # 🚪 程序入口（启动参数 --legacy）
├── CurlGenerator.spec         # 📦 PyInstaller 打包配置
├── logo.png                   # 🎨 应用图标 / 品牌标识
├── README.md                  # 📖 本文档
├── dist/
│   └── CurlGenerator.exe      # 🎁 打包成品（单文件）
└── curlgen/                   # 🧩 核心包（__version__ = 2.0.0）
    ├── __init__.py            # 版本号
    ├── theme.py               # 🎨 设计令牌 & 全局 QSS（深空座舱 / 晨雾）
    ├── config.py              # 💾 路径常量 & JSON 持久化（~/.cURL）
    ├── logging_setup.py       # 📝 按天滚动日志（保留 30 天）
    ├── signing.py             # 🔐 签名入口（MD5 / SHA256）
    ├── sign_utils.py          # 🔐 Java 风格排序 / SHA256 实现
    ├── curl_builder.py        # 🏗 cURL 命令构建（新旧 UI 共用）
    ├── ssh_executor.py        # 🖧 本地执行 + GateShell 堡垒机执行
    ├── widgets.py             # 🧱 通用组件（语法高亮 / 下拉 / 键值编辑器）
    ├── saved_panel.py         # ⭐ 快捷请求面板
    ├── dialogs.py             # 🪟 对话框（环境 / SSH / 保存请求）
    ├── main_window.py         # 🖥 旧版主窗口（经典 PyQt）
    ├── web_ui.py              # 🌌 新版主窗口（星轨控制台 + 本地 API）
    └── web/                   # 🌐 前端资源（运行时内联进 HTML）
        ├── index.html         # 结构骨架
        ├── app.css            # 样式（深/浅双主题令牌）
        ├── app.js             # 交互逻辑（高亮 / 悬浮 / 事件流）
        └── logo.png
```

---

## 🔬 核心机制详解

### 1. 环境变量引用 `{{ref}}`

在任何文本字段（URL、Header 值、Body、代理）中写 `{{变量名}}`，生成时会替换为 **当前环境** 下配置的 `自定义变量` 值。变量定义在 **环境配置管理** 的「自定义变量」列，格式为 `k1=v1; k2=v2`。

**替换规则**（与 Java 后端保持完全一致）：

```python
# curlgen/curl_builder.py
for k, v in var_map.items():
    text = text.replace("{{" + k + "}}", v)   # 原始键名精确匹配，不做 trim
```

**编辑器内的可视化增强**（新版 UI 独有）：

- ✅ **实时高亮**：所有 `{{...}}` 会被染色加底，一眼可辨。
- ✅ **悬浮取值**：鼠标悬停到引用上，气泡直接显示该变量 **在当前环境下将替换成的真实值**；未定义的引用显示 `（未定义）`，避免"看着像配了其实没配"的坑。
- 🧠 高亮层由透明 `textarea` + 下层 `pre` 覆盖层共同实现，字体度量严格对齐，输入体验与看板渲染两不误。

```text
输入:  https://{{host}}/api/pay?appKey={{appKey}}
生产环境:  host=api.prod.com  appKey=AK2026  →
实际:  https://api.prod.com/api/pay?appKey=AK2026
```

### 2. 签名计算（Java 兼容）

签名字符串构造遵循 Java 侧约定，**键名不区分大小写排序**，过滤空值与签名字段本身：

```text
sorted(key1=value1&key2=value2&...) + "key={secret}"
```

| 算法 | 实现 | 输出 |
| --- | --- | --- |
| `MD5` | `md5(sign_str).hexdigest().upper()` | 32 位大写十六进制 |
| `SHA256` | Java `MessageDigest("SHA-256")` 风格 | 64 位大写十六进制 |

**签名值落点：**

- `GET / HEAD / OPTIONS`：签名作为 URL 参数追加（`&sign=...`）。
- 其他方法：签名 **并入 Body**（JSON 注入字段、Form 追加行、Raw 追加 `&sign=`）。

签名计算会合并 **Query 参数 + Body 参数**（JSON/Form/Raw 自动解析展开），保证前后端参与的字段集合一致。接口预览区会实时显示 `签名: XXXXXXXX`。

### 3. GateShell 堡垒机远程执行

工具对接的是 **GateShell 菜单式堡垒机**（协议标识 `SSH-2.0-SSHD_*`）。它与普通 OpenSSH 跳板机有 **三处根本差异**，本项目已针对性适配：

| 差异 | 现象 | 本项目对策 |
| --- | --- | --- |
| ❌ 不支持 SSH `exec` | 通道被立即关闭，`exit_status=-1` | 放弃 exec，改用交互式 shell |
| ❌ 不做原始 TCP 透传 | `direct-tcpip` 回连指纹与堡垒机自身一致（接管而非转发） | 放弃嵌套隧道，走菜单 |
| ✅ 唯一入口是交互式菜单 | `invoke_shell()` 得到 TUI 授权机列表 | **菜单 → 搜索 → 回车进入目标机** |

**执行流程：**

```text
密码认证 → invoke_shell 进入菜单
        → 输入 /{ip} 搜索目标机（逐字符输入，防吞键）
        → 回车进入目标机 shell（echo 哨兵确认可交互）
        → 哨兵包裹执行命令:
          echo __CG_S_xxx__; {cmd} 2>&1; __ec=$?; echo; echo __CG_E_xxx__$__ec
        → 解析 START/END 双哨兵，提取真实输出 + 精确退出码
```

**关键工程细节：**

- **双哨兵独占成行匹配**：避免命中终端回显的那行命令；`END` 前补一个换行，防止无尾换行的响应（如 JSON）粘连。
- **stderr 合并 stdout**：交互式 shell 两者都在 pty 上，无法分离。
- **ANSI 序列剥离**：正则清理 OSC / CSI / 字符集 / 单字符控制码，`\r` 归一为 `\n`。
- **远端 curl 自动加 `--max-time`**：防止目标机进程挂死（检测到 `curl` 且未设超时会自动追加）。
- **多目标机**：`targets[{label, host, port, user}]`，环境配置里按 **标签** 选用目标机。
- **连接测试**：可只测堡垒机登录，也可延伸到目标机执行 `hostname` 验证。
- **可停止 / 超时**：`stop_event` 贯穿读写，硬超时上限 120s。

### 4. 双 UI 与本地 API 服务

新版星轨控制台采用 **"Web 前端 + 本地 API"** 架构：

- Python 端启动 `ThreadingHTTPServer` 于 `127.0.0.1` **随机端口**，只监听本机，天然安全。
- 页面通过 `window.API_BASE` 与后端通信，执行输出经 **`/api/events` 事件流** 增量拉取刷新。
- 由于 `setHtml` 文档无法通过 `file://` 加载外部资源，**CSS/JS 在启动时被读取并内联** 进 HTML（同时替换 `__PORT__` 占位符）。

> ⚠️ **改前端后需重启应用**：`app.css` / `app.js` / `index.html` 的改动在下次启动时才会被内联生效。

**旧版兼容注意**：内置 QtWebEngine 的 Chromium 版本较老，前端 **避免使用 `inset` 简写、`color-mix()`** 等新特性（代码中已用兼容写法替代）。

---

## 🌐 本地 HTTP API 一览

| 方法 | 路由 | 说明 |
| --- | --- | --- |
| `GET` | `/api/state` | 全量状态（logo / 环境 / 快捷请求 / SSH 配置 / 就绪标记） |
| `GET` | `/api/envs` | 环境配置列表 |
| `GET` | `/api/ssh` | SSH 配置（不含密码） |
| `GET` | `/api/ssh_targets` | 目标机列表 |
| `GET` | `/api/history` | 生成历史 |
| `GET` | `/api/events?since=N` | 事件流增量（执行输出 / 通知 / 测试结果） |
| `POST` | `/api/generate` | 生成 cURL 命令并返回签名预览 |
| `POST` | `/api/execute` | 执行请求（`mode=local` / `ssh`） |
| `POST` | `/api/stop` | 停止当前执行 |
| `POST` | `/api/copy` | 写入系统剪贴板 |
| `POST` | `/api/save_request` | 保存/更新快捷请求 |
| `POST` | `/api/delete_request` | 删除快捷请求 |
| `POST` | `/api/clear_history` | 清空历史 |
| `POST` | `/api/save_envs` | 保存环境配置 |
| `POST` | `/api/save_ssh` | 保存 SSH 配置 |
| `POST` | `/api/test_ssh` | 触发连接测试（异步，结果走事件流） |
| `POST` | `/api/switch_legacy` | 切回旧版界面 |

---

## 💾 配置持久化

所有用户数据落在 **当前登录用户主目录** 的 `~/.cURL/`（Windows 为 `C:\Users\<你>\.cURL\`），与程序安装位置无关，升级/搬迁不丢配置。

| 文件 | 内容 |
| --- | --- |
| `env_configs.json` | 环境配置（别名 / 密钥 / 算法 / 参数名 / 变量 / 目标机） |
| `ssh_config.json` | 跳板机信息 + 多目标机列表 |
| `saved_requests.json` | 快捷请求分组与内容 |
| `sign_configs.json` | 签名相关配置 |
| `logs/curlgen.log` | 按天滚动日志，保留 30 天 |

> 🔒 **安全提醒**：签名密钥与 SSH 密码以明文存于 `~/.cURL/` 本地配置中，**请勿将该目录提交到任何仓库或共享**。

---

## ⌨️ 快捷键

| 快捷键 | 功能 |
| --- | --- |
| `Ctrl + Enter` | 生成 cURL |
| `Ctrl + Shift + Enter` | 执行请求 |

---

## 📦 打包发布

使用 PyInstaller 打包为 **单文件 exe**：

```bash
pyinstaller CurlGenerator.spec
```

`CurlGenerator.spec` 关键配置：

- **datas**：`curlgen/web` 前端资源 + `logo.png`
- **hiddenimports**：`PyQt5.QtWebEngineWidgets`、`PyQt5.QtWebChannel`（避免被静态分析漏掉）
- **单文件 / 无控制台 / UPX 压缩 / 图标 `logo.png`**

产物输出至 `dist/CurlGenerator.exe`。

---

## 🧰 技术栈

<div align="center">

| 层级 | 技术 |
| :---: | :--- |
| 语言 | Python 3 |
| GUI 框架 | PyQt5（Fusion 样式） |
| 新版渲染引擎 | QtWebEngine / Chromium |
| 前端 | 原生 HTML5 + CSS3 + Vanilla JS（零框架） |
| 网络 / 执行 | http.server、subprocess、socket |
| SSH | paramiko（堡垒机交互式会话） |
| 签名 | hashlib（MD5 / SHA256，Java 风格） |
| 打包 | PyInstaller |

</div>

---

## ❓ 常见问题 FAQ

<details>
<summary><b>启动后是旧版界面，怎么用新版？</b></summary>

新版需要 PyQtWebEngine，请安装：`pip install PyQtWebEngine`。若已安装仍为旧版，检查是否有 `--legacy` 参数，或从菜单栏「视图 → 切换到新版界面」。
</details>

<details>
<summary><b>改了 app.js / app.css 为什么不生效？</b></summary>

前端资源在应用启动时被内联进 HTML，**必须重启程序** 才会加载新内容。
</details>

<details>
<summary><b>SSH 执行报"不是 GateShell 类型堡垒机"？</b></summary>

本工具针对 **菜单式堡垒机** 适配。若你的跳板机是标准 OpenSSH 且支持 `exec`，并不需要此处的菜单流程，可在其 shell 内直接使用系统 `ssh`。
</details>

<details>
<summary><b>提示"授权列表中没有某 IP"？</b></summary>

目标机必须已在堡垒机侧 **授权给你的账号**。可在 SSH 设置里点「测试目标机」，或查看菜单中的授权机器总数。
</details>

<details>
<summary><b>`{{变量}}` 没有被替换？</b></summary>

请确认：① 已在「环境管理」中为 **当前选中的环境** 配好该变量；② 变量名拼写与 `{{}}` 内完全一致（区分大小写、含空格）；③ 顶部环境下拉确实选中了对应环境。悬停引用时气泡显示 `（未定义）` 即说明未命中。
</details>

<details>
<summary><b>配置存在哪？换电脑怎么迁移？</b></summary>

全部在 `~/.cURL/`。拷贝该目录到新机器同名路径即可无缝迁移。
</details>

---

## 🗺 开发路线图

- [ ] 环境变量 `{{ref}}` 支持默认值与嵌套引用
- [ ] 请求集合导入 / 导出（Postman / HAR）
- [ ] 响应 JSON 结构化折叠视图
- [ ] 签名算法扩展（HMAC 系列）
- [ ] 多语言界面（EN / 中）

---

<div align="center">

**cURL Generator · 星轨控制台** &nbsp;v2.0.0

用一座座舱，装下接口调试的全部星光。⭐

<sub>Made with PyQt5 + QtWebEngine · 深空座舱 / 晨雾 双主题</sub>

</div>