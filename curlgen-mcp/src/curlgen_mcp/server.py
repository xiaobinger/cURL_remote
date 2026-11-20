"""MCP tools exposed by curlgen-mcp.

The server is intentionally stateful only for the GateShell menu workflow:
``bastion_login`` → ``bastion_list_hosts`` → ``bastion_connect`` →
``bastion_exec`` / ``bastion_upload`` / ``bastion_download``. All other tools
are one-shot and open/close their own connection.
"""

import json
import logging
import os
import sys
import threading
import warnings
from typing import Any

# Pydantic Settings emits this on Python 3.14 while the MCP SDK is imported; it
# does not affect server behaviour and would otherwise pollute the stdio stderr log.
warnings.filterwarnings(
    "ignore", message="Field 'lifespan' has an incomplete definition.*"
)

from . import __version__, codegen, config, paths
from .gate import GateError, GateTimeout, open_session
from .http import (
    HTTPFailure,
    build_remote_curl_script,
    parse_remote_output,
    perform_http,
)
from .mcp_compat import create_server
from .request import build_request
from .ssh import StandardSSH

logger = logging.getLogger("curlgen_mcp.server")

mcp = create_server(
    "curlgen-mcp",
    instructions=(
        "HTTP request execution, signed request code generation, standard SSH "
        "jump-host execution/SFTP, and GateShell bastion menu sessions with "
        "command execution and base64 file transfer. Secrets stay in ~/.cURL "
        "or environment variables and are never returned by list tools."
    ),
    version=__version__,
)

_BASTION_LOCK = threading.RLock()
_BASTION = {
    "session": None,
    "target": "",
    "label": "",
    "jump": {},
}

MAX_TIMEOUT = 600


def _pairs(value):
    if not value:
        return []
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        raise ValueError("headers/params 必须是对象或键值对数组")
    result = []
    for key, item in items:
        if isinstance(item, bool):
            item = "true" if item else "false"
        elif isinstance(item, (dict, list)):
            item = json.dumps(item, separators=(",", ":"), ensure_ascii=False)
        elif item is None:
            item = ""
        result.append([str(key), str(item)])
    return result


def _body_value(body, body_type):
    if body is None:
        return None
    if body_type == "json":
        return body
    if body_type == "form":
        if isinstance(body, dict):
            return "\n".join(f"{key}={value}" for key, value in body.items())
        if isinstance(body, (list, tuple)):
            return "\n".join(f"{key}={value}" for key, value in body)
        return str(body)
    if isinstance(body, (dict, list)):
        if isinstance(body, dict):
            return "&".join(f"{key}={value}" for key, value in body.items())
        return "&".join(f"{key}={value}" for key, value in body)
    return str(body)


def _body_type(value):
    normalized = str(value or "json").strip().lower().replace("_", " ")
    mapping = {
        "json": "json",
        "form": "form",
        "form data": "form",
        "form-data": "form",
        "raw": "raw",
        "raw text": "raw",
        "text": "raw",
        "none": "none",
        "": "none",
    }
    if normalized not in mapping:
        raise ValueError(f"不支持的 body_type: {value!r}")
    return mapping[normalized]


def build_spec(
    *,
    url,
    method="GET",
    headers=None,
    params=None,
    body=None,
    body_type="json",
    environment="",
    auth_type="none",
    auth_token="",
    auth_username="",
    auth_password="",
    auth_header="X-API-Key",
    sign_placement="auto",
    sign_enabled=None,
    sign_field="",
    sign_header="X-Sign",
    sign_algo="",
    sign_key="",
    verify_ssl=True,
    proxy="",
    timeout=30,
    follow_redirects=True,
):
    canonical_body_type = _body_type(body_type)
    auth_kind = str(auth_type or "none").strip().lower().replace("-", "_")
    auth = {"type": auth_kind}
    if auth_kind in ("bearer", "bearer_token"):
        auth["token"] = auth_token
    elif auth_kind in ("basic", "basic_auth"):
        auth["username"] = auth_username
        auth["password"] = auth_password
    elif auth_kind in ("api_key", "apikey"):
        auth["header"] = auth_header or "X-API-Key"
        auth["token"] = auth_token

    sign = {
        "placement": str(sign_placement or "auto").strip().lower(),
        "header": sign_header or "X-Sign",
    }
    if sign_enabled is not None:
        sign["enabled"] = bool(sign_enabled)
    if sign_field:
        sign["field"] = sign_field
    if sign_algo:
        sign["algo"] = sign_algo
    if sign_key:
        sign["secret"] = sign_key

    return {
        "method": str(method or "GET").strip().upper(),
        "url": str(url or "").strip(),
        "headers": _pairs(headers),
        "params": _pairs(params),
        "body": _body_value(body, canonical_body_type),
        "body_type": canonical_body_type,
        "environment": str(environment or "").strip(),
        "auth": auth,
        "sign": sign,
        "verify_ssl": bool(verify_ssl),
        "proxy": str(proxy or "").strip(),
        "timeout": float(timeout),
        "follow_redirects": bool(follow_redirects),
    }


def _public_request(req):
    sign = dict(req.get("sign") or {})
    sign.pop("secret", None)
    return {
        "method": req.get("method"),
        "url": req.get("url"),
        "headers": req.get("headers", {}),
        "body": req.get("body"),
        "body_type": req.get("body_type"),
        "params": req.get("params", []),
        "sign": sign,
        "environment": req.get("environment", ""),
        "verify_ssl": req.get("verify_ssl", True),
        "timeout": req.get("timeout", 30),
        "proxy": req.get("proxy", ""),
        "follow_redirects": req.get("follow_redirects", True),
    }


def _curl_preview(req):
    return codegen.generate_code(req, "curl", "static")["code"]


def _timeout(value):
    number = float(value)
    if number <= 0:
        raise ValueError("timeout 必须大于 0")
    return min(number, MAX_TIMEOUT)


def _fail(exc):
    if isinstance(exc, (GateTimeout,)):
        logger.warning("工具超时: %s", exc)
    return {"ok": False, "error": str(exc)}


def _resolve_target(label=""):
    data = config.load_ssh_config()
    return config.resolve_target(data, label or None)


@mcp.tool()
def list_environments() -> dict:
    """List signing environments, SSH targets, supported languages and limits. Secrets are never returned."""
    try:
        envs = [config.public_environment(env) for env in config.load_envs()]
        ssh = config.public_ssh_config(config.load_ssh_config())
        return {
            "ok": True,
            "config_dir": str(config.config_dir()),
            "default_environment": os.environ.get("CURLGEN_ENV", ""),
            "environments": envs,
            "ssh": ssh,
            "languages": list(codegen.LANGS),
            "runtime_signing_languages": list(codegen.RUNTIME_LANGS),
            "sign_placements": ["auto", "body", "header", "query", "none"],
            "limits": {
                "max_transfer_bytes": paths.max_transfer_bytes(),
                "chunk_size": paths.transfer_chunk_size(),
                "max_response_bytes": paths.max_response_bytes(),
            },
            "file_roots": [str(root) for root in paths.allowed_roots()],
        }
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    body: Any = None,
    body_type: str = "json",
    environment: str = "",
    auth_type: str = "none",
    auth_token: str = "",
    auth_username: str = "",
    auth_password: str = "",
    auth_header: str = "X-API-Key",
    sign_placement: str = "auto",
    sign_enabled: bool | None = None,
    sign_field: str = "",
    sign_header: str = "X-Sign",
    sign_algo: str = "",
    sign_key: str = "",
    verify_ssl: bool = True,
    proxy: str = "",
    timeout: float = 30,
    follow_redirects: bool = True,
) -> dict:
    """Send an HTTP request from the local machine. Supports env variables, auth and signing.

    sign_placement: auto (GET→query, body→body), body, header, query or none.
    sign_field/sign_header choose the field or header name.
    """
    try:
        spec = build_spec(
            url=url, method=method, headers=headers, params=params, body=body,
            body_type=body_type, environment=environment, auth_type=auth_type,
            auth_token=auth_token, auth_username=auth_username,
            auth_password=auth_password, auth_header=auth_header,
            sign_placement=sign_placement, sign_enabled=sign_enabled,
            sign_field=sign_field, sign_header=sign_header, sign_algo=sign_algo,
            sign_key=sign_key, verify_ssl=verify_ssl, proxy=proxy,
            timeout=_timeout(timeout), follow_redirects=follow_redirects,
        )
        req = build_request(spec)
        response = perform_http(req)
        return {
            "ok": True,
            "request": _public_request(req),
            "response": response,
            "curl": _curl_preview(req),
        }
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
def generate_code(
    url: str,
    method: str = "GET",
    headers: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    body: Any = None,
    body_type: str = "json",
    environment: str = "",
    auth_type: str = "none",
    auth_token: str = "",
    auth_username: str = "",
    auth_password: str = "",
    auth_header: str = "X-API-Key",
    sign_placement: str = "auto",
    sign_enabled: bool | None = None,
    sign_field: str = "",
    sign_header: str = "X-Sign",
    sign_algo: str = "",
    sign_key: str = "",
    verify_ssl: bool = True,
    proxy: str = "",
    timeout: float = 30,
    follow_redirects: bool = True,
    lang: str = "curl",
    sign_mode: str = "static",
) -> dict:
    """Generate request code in curl/python/javascript/typescript/node/java/csharp/go/php/rust.

    sign_mode=static embeds the computed signature; runtime regenerates it in
    Python, JavaScript, TypeScript, Node.js and Java (other languages fall back).
    """
    try:
        spec = build_spec(
            url=url, method=method, headers=headers, params=params, body=body,
            body_type=body_type, environment=environment, auth_type=auth_type,
            auth_token=auth_token, auth_username=auth_username,
            auth_password=auth_password, auth_header=auth_header,
            sign_placement=sign_placement, sign_enabled=sign_enabled,
            sign_field=sign_field, sign_header=sign_header, sign_algo=sign_algo,
            sign_key=sign_key, verify_ssl=verify_ssl, proxy=proxy,
            timeout=_timeout(timeout), follow_redirects=follow_redirects,
        )
        req = build_request(spec)
        result = codegen.generate_code(req, lang=lang, sign_mode=sign_mode)
        result["ok"] = True
        result["request"] = _public_request(req)
        return result
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
def ssh_list_targets() -> dict:
    """List jump host and target machines from ~/.cURL/ssh_config.json (passwords masked)."""
    try:
        ssh = config.public_ssh_config(config.load_ssh_config())
        for target in ssh["targets"]:
            target["standard_ssh_ready"] = bool(
                ssh["jump_password_set"] and target["host"]
            )
            target["gateshell_ready"] = bool(ssh["jump_password_set"] and target["host"])
        return {"ok": True, "jump": ssh, "targets": ssh["targets"]}
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
def ssh_exec(command: str, target: str = "", timeout: float = 60) -> dict:
    """Execute a command on a target through a standard OpenSSH jump host."""
    client = None
    try:
        cfg = _resolve_target(target)
        client = StandardSSH(cfg)
        client.connect()
        result = client.exec(command, timeout=_timeout(timeout))
        return {
            "ok": True,
            "target": cfg.get("label") or cfg.get("host", ""),
            "result": result,
        }
    except Exception as exc:
        return _fail(exc)
    finally:
        if client is not None:
            client.close()


@mcp.tool()
def ssh_upload(local_path: str, remote_path: str, target: str = "") -> dict:
    """Upload a local file to a target through a standard SSH jump host (SFTP)."""
    client = None
    try:
        cfg = _resolve_target(target)
        client = StandardSSH(cfg)
        client.connect()
        result = client.upload(local_path, remote_path)
        return {
            "ok": True,
            "target": cfg.get("label") or cfg.get("host", ""),
            "transfer": result,
        }
    except Exception as exc:
        return _fail(exc)
    finally:
        if client is not None:
            client.close()


@mcp.tool()
def ssh_download(remote_path: str, local_path: str, target: str = "") -> dict:
    """Download a file from a target through a standard SSH jump host (SFTP)."""
    client = None
    try:
        cfg = _resolve_target(target)
        client = StandardSSH(cfg)
        client.connect()
        result = client.download(remote_path, local_path)
        return {
            "ok": True,
            "target": cfg.get("label") or cfg.get("host", ""),
            "transfer": result,
        }
    except Exception as exc:
        return _fail(exc)
    finally:
        if client is not None:
            client.close()


@mcp.tool()
def remote_http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    body: Any = None,
    body_type: str = "json",
    environment: str = "",
    auth_type: str = "none",
    auth_token: str = "",
    auth_username: str = "",
    auth_password: str = "",
    auth_header: str = "X-API-Key",
    sign_placement: str = "auto",
    sign_enabled: bool | None = None,
    sign_field: str = "",
    sign_header: str = "X-Sign",
    sign_algo: str = "",
    sign_key: str = "",
    verify_ssl: bool = True,
    proxy: str = "",
    timeout: float = 30,
    follow_redirects: bool = True,
    target: str = "",
    transport: str = "gateshell",
) -> dict:
    """Execute an HTTP request from a remote target via GateShell or standard SSH curl.

    transport=gateshell uses the bastion menu; transport=ssh uses a standard jump host.
    """
    session = None
    try:
        spec = build_spec(
            url=url, method=method, headers=headers, params=params, body=body,
            body_type=body_type, environment=environment, auth_type=auth_type,
            auth_token=auth_token, auth_username=auth_username,
            auth_password=auth_password, auth_header=auth_header,
            sign_placement=sign_placement, sign_enabled=sign_enabled,
            sign_field=sign_field, sign_header=sign_header, sign_algo=sign_algo,
            sign_key=sign_key, verify_ssl=verify_ssl, proxy=proxy,
            timeout=_timeout(timeout), follow_redirects=follow_redirects,
        )
        req = build_request(spec)
        script = build_remote_curl_script(req)
        cfg = _resolve_target(target)
        mode = str(transport or "gateshell").strip().lower()
        if mode in ("gateshell", "gate", "bastion"):
            session = open_session(cfg)
            session.connect_host(cfg["host"])
            exit_code, output = session.run(script, timeout=req["timeout"] + 30)
            result = {
                "exit_code": exit_code,
                "stdout": output,
                "stderr": "",
            }
        elif mode in ("ssh", "standard"):
            client = StandardSSH(cfg)
            try:
                client.connect()
                result = client.exec(script, timeout=req["timeout"] + 30)
            finally:
                client.close()
        else:
            raise ValueError("transport 只能是 gateshell 或 ssh")

        if result["exit_code"] == 63:
            raise HTTPFailure(f"远端响应超过大小限制 {paths.max_response_bytes()} 字节")
        if result["exit_code"] != 0:
            detail = (result.get("stderr") or result.get("stdout") or "").strip()[-300:]
            raise HTTPFailure(
                f"远端 curl 失败（exit={result['exit_code']}）"
                + (f": {detail}" if detail else "")
            )
        response = parse_remote_output(result["stdout"])
        response["transport"] = (
            "gateshell-curl" if mode in ("gateshell", "gate", "bastion") else "ssh-curl"
        )
        return {
            "ok": True,
            "target": cfg.get("label") or cfg.get("host", ""),
            "request": _public_request(req),
            "response": response,
            "curl": _curl_preview(req),
        }
    except Exception as exc:
        return _fail(exc)
    finally:
        if session is not None:
            session.close()


# ──────────────────────────── GateShell stateful tools ────────────────────────────
def _require_session():
    session = _BASTION.get("session")
    if session is None:
        raise GateError("尚未登录堡垒机，请先调用 bastion_login")
    return session


@mcp.tool()
def bastion_login() -> dict:
    """Log in to the GateShell bastion and keep the menu session for later tools."""
    with _BASTION_LOCK:
        old = _BASTION.get("session")
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        try:
            cfg = config.load_ssh_config()
            session = open_session(cfg)
            _BASTION.update(
                {
                    "session": session,
                    "target": "",
                    "label": "",
                    "jump": {
                        "jump_host": cfg.get("jump_host", ""),
                        "jump_port": cfg.get("jump_port", "22"),
                        "jump_user": cfg.get("jump_user", ""),
                    },
                }
            )
            total = session.total_hosts()
            return {
                "ok": True,
                "jump": _BASTION["jump"],
                "total_hosts": total,
                "message": "堡垒机登录成功，请调用 bastion_list_hosts 选择机器",
            }
        except Exception as exc:
            _BASTION.update({"session": None, "target": "", "label": "", "jump": {}})
            return _fail(exc)


@mcp.tool()
def bastion_list_hosts() -> dict:
    """List authorized machines from the logged-in GateShell menu."""
    with _BASTION_LOCK:
        try:
            session = _require_session()
            hosts = session.list_hosts()
            return {
                "ok": True,
                "total": len(hosts),
                "hosts": hosts,
                "jump": _BASTION.get("jump", {}),
            }
        except Exception as exc:
            return _fail(exc)


@mcp.tool()
def bastion_connect(host: str) -> dict:
    """Enter one authorized machine by IP or menu name for subsequent bastion_exec."""
    with _BASTION_LOCK:
        try:
            session = _require_session()
            found = session.find_host(host)
            if found is None:
                raise GateError(f"授权列表中没有 {host}")
            if _BASTION.get("target") == found["ip"]:
                return {
                    "ok": True,
                    "host": found,
                    "target": found["ip"],
                    "already_connected": True,
                }
            if _BASTION.get("target"):
                # A GateShell session cannot jump between target shells in
                # place, so reopen the bastion menu before entering another.
                old = _BASTION.get("session")
                if old is not None:
                    old.close()
                _BASTION.update({"session": None, "target": "", "label": ""})
                session = open_session(config.load_ssh_config())
                _BASTION["session"] = session
            session.connect_host(found["ip"])
            _BASTION["target"] = found["ip"]
            _BASTION["label"] = found["name"]
            return {"ok": True, "host": found, "target": found["ip"]}
        except Exception as exc:
            return _fail(exc)


@mcp.tool()
def bastion_exec(command: str, timeout: float = 60) -> dict:
    """Execute a command in the current bastion target shell (stderr merged into stdout)."""
    with _BASTION_LOCK:
        try:
            session = _require_session()
            if not _BASTION.get("target"):
                raise GateError("尚未选择目标机，请先调用 bastion_connect")
            exit_code, output = session.run(command, timeout=_timeout(timeout))
            return {
                "ok": True,
                "target": _BASTION["target"],
                "label": _BASTION.get("label", ""),
                "exit_code": exit_code,
                "stdout": output,
                "stderr": "",
            }
        except Exception as exc:
            return _fail(exc)


@mcp.tool()
def bastion_status() -> dict:
    """Return the current GateShell login/target state."""
    with _BASTION_LOCK:
        session = _BASTION.get("session")
        return {
            "ok": True,
            "logged_in": session is not None,
            "target": _BASTION.get("target", ""),
            "label": _BASTION.get("label", ""),
            "jump": _BASTION.get("jump", {}),
        }


@mcp.tool()
def bastion_logout() -> dict:
    """Close the GateShell session and clear the selected target."""
    with _BASTION_LOCK:
        session = _BASTION.get("session")
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        _BASTION.update({"session": None, "target": "", "label": "", "jump": {}})
        return {"ok": True, "logged_in": False}


@mcp.tool()
def bastion_upload(local_path: str, remote_path: str) -> dict:
    """Upload a local file to the current bastion target using base64 chunks."""
    with _BASTION_LOCK:
        try:
            session = _require_session()
            if not _BASTION.get("target"):
                raise GateError("尚未选择目标机，请先调用 bastion_connect")
            data = paths.read_local_file(local_path)
            result = session.upload(data, remote_path)
            return {"ok": True, "target": _BASTION["target"], "transfer": result}
        except Exception as exc:
            return _fail(exc)


@mcp.tool()
def bastion_download(remote_path: str, local_path: str) -> dict:
    """Download a file from the current bastion target and write it below an allowed root."""
    with _BASTION_LOCK:
        try:
            session = _require_session()
            if not _BASTION.get("target"):
                raise GateError("尚未选择目标机，请先调用 bastion_connect")
            result = session.download(remote_path)
            saved = paths.write_local_file(local_path, result["data"])
            return {
                "ok": True,
                "target": _BASTION["target"],
                "transfer": {
                    key: value
                    for key, value in result.items()
                    if key != "data"
                },
                "local_path": str(saved),
            }
        except Exception as exc:
            return _fail(exc)


def main():
    logging.basicConfig(
        level=os.environ.get("CURLGEN_MCP_LOG_LEVEL", "INFO").upper(),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()