"""Read the desktop app's ``~/.cURL`` configuration.

The MCP server is read-only: it never rewrites environment or SSH files. All
paths and values are resolved at call time so tests and container deployments
can override them with ``CURLGEN_CONFIG_DIR`` and ``CURLGEN_*`` variables.
"""

import copy
import json
import os
from pathlib import Path


def config_dir() -> Path:
    raw = (os.environ.get("CURLGEN_CONFIG_DIR") or "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".cURL"


def env_config_file() -> Path:
    return config_dir() / "env_configs.json"


def ssh_config_file() -> Path:
    return config_dir() / "ssh_config.json"


def _read_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def load_envs():
    data = _read_json(env_config_file(), [])
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def find_environment(alias):
    if not alias or alias == "(不选择环境)":
        return None
    return next((env for env in load_envs() if env.get("alias") == alias), None)


def default_environment():
    alias = (os.environ.get("CURLGEN_ENV") or "").strip()
    return find_environment(alias) if alias else None


def public_environment(env):
    """Return an environment description that never exposes secret values."""
    if not isinstance(env, dict):
        return {}
    variables = []
    for pair in env.get("custom_params") or []:
        if isinstance(pair, (list, tuple)) and pair and pair[0]:
            variables.append(str(pair[0]))
    return {
        "alias": env.get("alias", ""),
        "signing_enabled": bool(env.get("enable_sign", False)),
        "sign_algorithm": env.get("algo", "MD5"),
        "sign_field": env.get("param_name", "sign"),
        "has_secret_key": bool(env.get("key")),
        "ssh_enabled": bool(env.get("use_ssh", False)),
        "ssh_target": env.get("ssh_target", ""),
        "variables": variables,
        "variable_count": len(variables),
    }


def normalize_ssh_config(data):
    """Normalize legacy single-target config to ``targets[]``."""
    if not isinstance(data, dict):
        data = {}
    data = copy.deepcopy(data)
    targets = data.get("targets")
    if not targets:
        host = str(data.get("target_host") or "").strip()
        targets = []
        if host:
            targets.append(
                {
                    "label": data.get("target_label") or "默认目标机",
                    "host": host,
                    "port": data.get("target_port") or "22",
                    "user": data.get("target_user") or "",
                }
            )
    clean = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        host = str(target.get("host") or "").strip()
        if not host:
            continue
        label = str(target.get("label") or host).strip() or host
        item = {
            "label": label,
            "host": host,
            "port": str(target.get("port") or "22").strip() or "22",
            "user": str(target.get("user") or "").strip(),
        }
        if target.get("password"):
            item["password"] = str(target["password"])
        if target.get("key"):
            item["key"] = str(target["key"])
        clean.append(item)
    data["targets"] = clean
    return data


def _apply_env_overrides(data):
    if not isinstance(data, dict):
        data = {}
    data = normalize_ssh_config(data)

    mapping = {
        "jump_host": "CURLGEN_JUMP_HOST",
        "jump_port": "CURLGEN_JUMP_PORT",
        "jump_user": "CURLGEN_JUMP_USER",
        "jump_pass": "CURLGEN_JUMP_PASSWORD",
        "jump_key": "CURLGEN_JUMP_KEY",
    }
    for field, variable in mapping.items():
        value = os.environ.get(variable)
        if value is not None:
            data[field] = value if field in ("jump_pass", "jump_key") else value.strip()

    overrides = {
        "host": os.environ.get("CURLGEN_TARGET_HOST"),
        "port": os.environ.get("CURLGEN_TARGET_PORT"),
        "user": os.environ.get("CURLGEN_TARGET_USER"),
        "password": os.environ.get("CURLGEN_TARGET_PASSWORD"),
        "key": os.environ.get("CURLGEN_TARGET_KEY"),
    }
    if any(value is not None and str(value).strip() for value in overrides.values()):
        targets = data.get("targets") or []
        target = targets[0] if targets else {
            "label": "环境变量目标机",
            "host": "",
            "port": "22",
            "user": "",
        }
        for field, value in overrides.items():
            if value is not None:
                target[field] = str(value).strip()
        target["port"] = target.get("port") or "22"
        data["targets"] = [target]
    return data


def load_ssh_config():
    return _apply_env_overrides(_read_json(ssh_config_file(), {}))


def resolve_target(data, label=None):
    """Resolve a target label to jump + target fields (first target as fallback)."""
    normalized = _apply_env_overrides(data)
    targets = normalized.get("targets") or []
    target = None
    if label:
        target = next((item for item in targets if item["label"] == label), None)
    if target is None and targets:
        target = targets[0]
    return {
        "label": target["label"] if target else "",
        "host": target["host"] if target else "",
        "port": target["port"] if target else "22",
        "user": target["user"] if target else "",
        "target_pass": (
            str(target.get("password") or "")
            if target
            else ""
        ) or os.environ.get("CURLGEN_TARGET_PASSWORD", ""),
        "target_key": (
            str(target.get("key") or "")
            if target
            else ""
        ) or os.environ.get("CURLGEN_TARGET_KEY", ""),
        "jump_host": normalized.get("jump_host", ""),
        "jump_port": str(normalized.get("jump_port") or "22"),
        "jump_user": normalized.get("jump_user", ""),
        "jump_pass": normalized.get("jump_pass", ""),
        "jump_key": normalized.get("jump_key", "")
        or os.environ.get("CURLGEN_JUMP_KEY", ""),
    }


def public_ssh_config(data):
    normalized = _apply_env_overrides(data)
    targets = []
    for target in normalized.get("targets", []):
        targets.append(
            {
                "label": target.get("label", ""),
                "host": target.get("host", ""),
                "port": target.get("port", "22"),
                "user": target.get("user", ""),
                "target_password_set": bool(target.get("password")),
                "target_key_set": bool(target.get("key")),
            }
        )
    return {
        "jump_host": normalized.get("jump_host", ""),
        "jump_port": str(normalized.get("jump_port") or "22"),
        "jump_user": normalized.get("jump_user", ""),
        "jump_password_set": bool(normalized.get("jump_pass")),
        "jump_key_set": bool(normalized.get("jump_key")),
        "targets": targets,
    }