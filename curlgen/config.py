"""路径常量与 JSON 配置持久化"""
import os
import sys
import json
import logging

logger = logging.getLogger("curlgen.config")

# 应用根目录（存放 logo 等资源）：
#   - 打包为 exe 时 → exe 所在目录
#   - 源码运行时    → curlgen/ 的上一级（与 curl_generator.py 同级）
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = APP_DIR  # 兼容旧引用

# 配置/日志目录：当前登录用户主目录下的 .cURL（不随项目/安装位置变化）
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".cURL")
os.makedirs(CONFIG_DIR, exist_ok=True)

ENV_CONFIG_FILE = os.path.join(CONFIG_DIR, "env_configs.json")
SSH_CONFIG_FILE = os.path.join(CONFIG_DIR, "ssh_config.json")
SAVED_REQUESTS_FILE = os.path.join(CONFIG_DIR, "saved_requests.json")
SIGN_CONFIG_FILE = os.path.join(CONFIG_DIR, "sign_configs.json")
TODO_FILE = os.path.join(CONFIG_DIR, "todos.json")
STOCKS_FILE = os.path.join(CONFIG_DIR, "stocks.json")
UI_PREFS_FILE = os.path.join(CONFIG_DIR, "ui_prefs.json")
LOG_DIR = os.path.join(CONFIG_DIR, "logs")


# ──────────────────────────── 环境配置持久化 ────────────────────────────
def load_env_configs():
    if os.path.exists(ENV_CONFIG_FILE):
        try:
            with open(ENV_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info("已加载环境配置: %s (%d 条)", ENV_CONFIG_FILE, len(data))
            return data
        except Exception as e:
            logger.error("加载环境配置失败: %s", e)
    return []


def save_env_configs(configs):
    try:
        with open(ENV_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(configs, f, ensure_ascii=False, indent=2)
        logger.info("已保存环境配置: %s (%d 条)", ENV_CONFIG_FILE, len(configs))
    except Exception as e:
        logger.error("保存环境配置失败: %s", e)
        raise


def load_ssh_config():
    if os.path.exists(SSH_CONFIG_FILE):
        try:
            with open(SSH_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("加载 SSH 配置失败: %s", e)
    return {}


def save_ssh_config(data):
    try:
        with open(SSH_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("已保存 SSH 配置: jump=%s@%s:%s target=%s@%s:%s",
                    data.get("jump_user", ""), data.get("jump_host", ""), data.get("jump_port", ""),
                    data.get("target_user", ""), data.get("target_host", ""), data.get("target_port", ""))
    except Exception as e:
        logger.error("保存 SSH 配置失败: %s", e)
        raise


def load_saved_requests():
    if os.path.exists(SAVED_REQUESTS_FILE):
        try:
            with open(SAVED_REQUESTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("加载快捷请求失败: %s", e)
    return {"groups": []}


def save_saved_requests(data):
    try:
        with open(SAVED_REQUESTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("保存快捷请求失败: %s", e)
        raise


# ──────────────────────────── 待办（首页仪表盘） ────────────────────────────
def load_todos():
    """[{id, date(YYYY-MM-DD), text, done, created}]"""
    if os.path.exists(TODO_FILE):
        try:
            with open(TODO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception as e:
            logger.error("加载待办失败: %s", e)
    return []


def save_todos(data):
    try:
        with open(TODO_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("保存待办失败: %s", e)
        raise


# ──────────────────────────── 自选股（首页仪表盘行情/K线） ────────────────────────────
DEFAULT_STOCKS = [{"code": "sh000001", "name": "上证指数"}]


def load_stocks():
    """[{code(带sh/sz前缀), name}]；为空时回落默认上证指数"""
    if os.path.exists(STOCKS_FILE):
        try:
            with open(STOCKS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return data
        except Exception as e:
            logger.error("加载自选股失败: %s", e)
    return list(DEFAULT_STOCKS)


def save_stocks(data):
    try:
        with open(STOCKS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("保存自选股失败: %s", e)
        raise


# ──────────────────────────── UI 偏好（主题 / 默认行情标的 / Dashboard 布局） ────────────────────────────
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


def load_ui_prefs():
    """{"theme": "dark", "stock": "sh000001"}；WebEngine 的 localStorage 不保证持久化，走文件"""
    if os.path.exists(UI_PREFS_FILE):
        try:
            with open(UI_PREFS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.error("加载 UI 偏好失败: %s", e)
    return {}


def save_ui_prefs(data):
    try:
        with open(UI_PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("保存 UI 偏好失败: %s", e)
        raise


# ──────────────────────────── SSH 多目标机 ────────────────────────────
def normalize_ssh_config(data):
    """兼容旧版单目标机格式：target_host* → targets[{label,host,port,user}]"""
    if not isinstance(data, dict):
        data = {}
    targets = data.get("targets")
    if not targets:
        host = (data.get("target_host") or "").strip()
        targets = []
        if host:
            targets.append({
                "label": data.get("target_label") or "默认目标机",
                "host": host,
                "port": data.get("target_port") or "22",
                "user": data.get("target_user") or "",
            })
    clean = []
    for t in targets:
        t = t or {}
        host = (t.get("host") or "").strip()
        if not host:
            continue
        clean.append({
            "label": (t.get("label") or host).strip() or host,
            "host": host,
            "port": str(t.get("port") or "22").strip() or "22",
            "user": (t.get("user") or "").strip(),
        })
    data["targets"] = clean
    return data


def load_ssh_targets():
    """返回目标机列表 [{label, host, port, user}]"""
    return normalize_ssh_config(load_ssh_config()).get("targets", [])


def resolve_ssh_cfg(ssh_data, label=None, target_index=None):
    """按目标机标签解析完整配置；有索引时同时校验索引与标签。
    旧调用未传索引时保持原标签解析行为。"""
    data = normalize_ssh_config(ssh_data)
    targets = data.get("targets", [])
    target = None
    indexed = type(target_index) is int and 0 <= target_index < len(targets)
    if indexed:
        candidate = targets[target_index]
        if not label or candidate["label"] == label:
            target = candidate
    elif target_index is None and label:
        target = next((t for t in targets if t["label"] == label), None)
    if target is None and targets and target_index is None:
        target = targets[0]
    cfg = {
        "jump_host": data.get("jump_host", ""),
        "jump_port": data.get("jump_port", ""),
        "jump_user": data.get("jump_user", ""),
        "jump_pass": data.get("jump_pass", ""),
        "target_host": target["host"] if target else "",
        "target_port": target["port"] if target else "",
        "target_user": target["user"] if target else "",
        "target_label": target["label"] if target else "",
    }
    return cfg


def env_ssh_target_alias(env_alias):
    """环境别名 → 其配置的 SSH 目标机标签；未启用 SSH 返回 None"""
    if not env_alias or env_alias == "(不选择环境)":
        return None
    for c in load_env_configs():
        if c.get("alias") == env_alias and c.get("use_ssh"):
            return c.get("ssh_target") or ""
    return None
