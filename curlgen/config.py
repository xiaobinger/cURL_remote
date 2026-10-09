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
