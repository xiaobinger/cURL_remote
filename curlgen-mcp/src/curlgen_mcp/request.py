"""Resolve a structured request and place its signature.

The public entry point is :func:`build_request`. It mirrors the desktop app's
``resolve_request`` but adds explicit signature placement (body / header /
query / auto) and keeps the result suitable for both local HTTP execution and
code generation.
"""

import base64
import json
import os
from urllib.parse import quote, urlencode

from . import config, signing

NO_BODY_METHODS = ("GET", "HEAD", "OPTIONS")
PLACEMENTS = ("auto", "body", "header", "query", "none")
DEFAULT_CONTENT_TYPE = {
    "json": "application/json",
    "form": "application/x-www-form-urlencoded",
    "raw": "application/x-www-form-urlencoded",
}


def _as_pairs(value):
    if value is None:
        return []
    if hasattr(value, "items"):
        return list(value.items())
    pairs = []
    for item in value:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            pairs.append((item[0], item[1]))
    return pairs


def _text(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def _resolve_vars(value, var_map):
    if not var_map or not isinstance(value, str):
        return value
    for key, replacement in var_map.items():
        value = value.replace("{{" + key + "}}", replacement)
    return value


def _resolve_deep(value, var_map):
    if isinstance(value, str):
        return _resolve_vars(value, var_map)
    if isinstance(value, list):
        return [_resolve_deep(item, var_map) for item in value]
    if isinstance(value, dict):
        return {
            _resolve_vars(str(key), var_map): _resolve_deep(item, var_map)
            for key, item in value.items()
        }
    return value


def _body_type(value):
    raw = str(value or "none").strip().lower()
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
    if raw not in mapping:
        raise ValueError(f"不支持的 body_type: {value!r}（json / form / raw / none）")
    return mapping[raw]


def _iter_header_pairs(value):
    if value is None:
        return []
    if hasattr(value, "items"):
        return list(value.items())
    return _as_pairs(value)


def _set_header(headers, name, value):
    lowered = name.lower()
    for existing in list(headers):
        if existing.lower() == lowered:
            headers[existing] = value
            return existing
    headers[name] = value
    return name


def _has_header(headers, name):
    lowered = name.lower()
    return any(key.lower() == lowered for key in headers)


def _append_query(url, pairs):
    if not pairs:
        return url
    encoded = urlencode([(str(k), _text(v)) for k, v in pairs], quote_via=quote)
    if "?" not in url:
        separator = "?"
    elif url.endswith("?") or url.endswith("&"):
        separator = ""
    else:
        separator = "&"
    return url + separator + encoded


def _parse_form_lines(text):
    pairs = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if "=" in line:
            key, value = line.split("=", 1)
            pairs.append((key.strip(), value.strip()))
    return pairs


def _form_pairs(body, body_type):
    if body_type == "form":
        if isinstance(body, dict):
            return [(str(k), _text(v)) for k, v in body.items()]
        return _parse_form_lines(body)
    if body_type == "raw":
        pairs = []
        for line in str(body or "").splitlines():
            for segment in line.split("&"):
                segment = segment.strip()
                if "=" in segment:
                    key, value = segment.split("=", 1)
                    pairs.append((key.strip(), value.strip()))
        return pairs
    return []


def _json_object(text):
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _sign_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if value is None:
        return None
    return value


def _sign_config(spec, env_config, method):
    raw = spec.get("sign") or {}
    if not isinstance(raw, dict):
        raw = {}

    placement = str(raw.get("placement") or "auto").strip().lower()
    if placement not in PLACEMENTS:
        raise ValueError(
            f"不支持的签名位置: {placement!r}（auto / body / header / query / none）"
        )

    enabled = raw.get("enabled")
    if placement == "none":
        enabled = False
    elif enabled is None:
        if env_config is not None:
            enabled = bool(env_config.get("enable_sign", False))
        else:
            enabled = bool(raw.get("secret"))

    field = (
        raw.get("field")
        or (env_config or {}).get("param_name")
        or "sign"
    )
    header = raw.get("header") or "X-Sign"
    algo = (
        raw.get("algo")
        or os.environ.get("CURLGEN_SIGN_ALGO")
        or (env_config or {}).get("algo")
        or "MD5"
    )
    if "secret" in raw and raw.get("secret") is not None:
        secret = raw.get("secret")
    else:
        secret = os.environ.get("CURLGEN_SIGN_KEY") or (env_config or {}).get("key", "")

    enabled = bool(enabled)
    if enabled and not secret:
        raise ValueError("签名已启用，但环境配置中没有密钥（key），也无法从 CURLGEN_SIGN_KEY 获取")

    algorithm = str(algo).strip().upper().replace("-", "") or "MD5"
    if algorithm not in ("MD5", "SHA256"):
        raise ValueError(f"不支持的签名算法: {algo!r}（仅支持 MD5 / SHA256）")

    effective = placement
    if enabled and placement == "auto":
        effective = "query" if method in NO_BODY_METHODS else "body"

    return {
        "enabled": enabled,
        "placement": effective if enabled else "none",
        "requested_placement": placement,
        "field": str(field),
        "header": str(header),
        "algo": algorithm,
        "secret": str(secret) if secret else "",
    }


def build_request(spec, env_config=None):
    """Resolve ``spec`` into a final request dictionary.

    ``spec`` keys: ``method``, ``url``, ``environment``, ``headers``, ``params``,
    ``body``, ``body_type``, ``auth``, ``sign``, ``verify_ssl``, ``timeout``,
    ``proxy`` and ``follow_redirects``.

    The return value contains the final URL, headers, body, body type, sign
    metadata and transport options. It is consumed by the HTTP client, remote
    executors and code generators.
    """
    method = str(spec.get("method") or "GET").strip().upper()
    environment = str(spec.get("environment") or "").strip()

    if env_config is None and environment:
        env_config = config.find_environment(environment)
        if env_config is None:
            raise ValueError(f"环境不存在: {environment}")
    if env_config is None and not environment:
        env_config = config.default_environment()

    var_map = {}
    if isinstance(env_config, dict):
        for key, value in env_config.get("custom_params") or []:
            if key:
                var_map[str(key)] = _text(value)

    url = _resolve_vars(str(spec.get("url") or "").strip(), var_map)
    if not url:
        raise ValueError("请输入 URL")

    headers = {}

    auth = spec.get("auth") or {}
    if not isinstance(auth, dict):
        auth = {}
    auth_type = str(auth.get("type") or "none").strip().lower().replace("-", "_")
    if auth_type in ("bearer", "bearer_token"):
        token = _resolve_vars(str(auth.get("token") or "").strip(), var_map)
        if token:
            _set_header(headers, "Authorization", f"Bearer {token}")
    elif auth_type in ("basic", "basic_auth"):
        username = _resolve_vars(str(auth.get("username") or "").strip(), var_map)
        password = _resolve_vars(str(auth.get("password") or ""), var_map)
        if username:
            encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode()
            _set_header(headers, "Authorization", f"Basic {encoded}")
    elif auth_type in ("api_key", "apikey"):
        header_name = str(auth.get("header") or "X-API-Key").strip() or "X-API-Key"
        token = _resolve_vars(str(auth.get("token") or "").strip(), var_map)
        if token:
            _set_header(headers, header_name, token)

    for key, value in _iter_header_pairs(spec.get("headers")):
        name = _resolve_vars(str(key), var_map).strip()
        if name:
            _set_header(headers, name, _resolve_vars(_text(value), var_map))

    params = [
        (_resolve_vars(str(key), var_map), _resolve_vars(_text(value), var_map))
        for key, value in _as_pairs(spec.get("params"))
    ]
    url = _append_query(url, params)

    sign = _sign_config(spec, env_config, method)
    body_type = _body_type(spec.get("body_type"))

    body_value = _resolve_deep(spec.get("body"), var_map)
    if body_value is None or body_value == "":
        body_text = ""
    elif body_type == "json" and isinstance(body_value, (dict, list)):
        body_text = json.dumps(body_value, ensure_ascii=False, indent=2)
    else:
        body_text = str(body_value)

    sign_params = {str(key): value for key, value in params}
    json_body = None
    if body_type == "json" and body_text:
        json_body = _json_object(body_text)
        if json_body is not None:
            for key, value in json_body.items():
                normalized = _sign_value(value)
                if normalized is not None:
                    sign_params[str(key)] = normalized
    elif body_type in ("form", "raw") and body_text:
        for key, value in _form_pairs(body_value, body_type):
            sign_params[key] = value

    sign_value = None
    if sign["enabled"]:
        sign_value = signing.compute_sign(
            sign_params, sign["secret"], sign["algo"], sign["field"]
        )
        if sign_value is None:
            raise ValueError("签名计算失败：密钥为空")

    sign_result = {
        "enabled": sign["enabled"],
        "value": sign_value or "",
        "placement": sign["placement"],
        "requested_placement": sign["requested_placement"],
        "field": sign["field"],
        "header": sign["header"],
        "algo": sign["algo"],
        # 仅代码生成使用；对外响应必须移除
        "secret": sign["secret"],
    }

    body = None
    if sign["enabled"] and sign["placement"] == "query":
        url = _append_query(url, [(sign["field"], sign_value)])
    elif sign["enabled"] and sign["placement"] == "header":
        _set_header(headers, sign["header"], sign_value)

    if sign["enabled"] and sign["placement"] == "body":
        if body_type == "json":
            if not body_text:
                json_body = {}
            elif json_body is None:
                raise ValueError("签名放在 Body 时，JSON 请求体必须是合法 JSON 对象")
            json_body[sign["field"]] = sign_value
            body = json.dumps(json_body, ensure_ascii=False, indent=2)
            body_type = "json"
        elif body_type == "form":
            pairs = _form_pairs(body_value, body_type)
            pairs.append((sign["field"], sign_value))
            body = "&".join(f"{key}={value}" for key, value in pairs)
            body_type = "form"
        elif body_type == "raw":
            body = body_text
            if body and not body.endswith("&"):
                body += "&"
            body += f"{sign['field']}={sign_value}"
            body_type = "raw"
        else:
            body = json.dumps({sign["field"]: sign_value}, ensure_ascii=False, indent=2)
            body_type = "json"
    elif body_type != "none" and body_text:
        body = body_text

    if body is not None and body != "" and body_type in DEFAULT_CONTENT_TYPE:
        if not _has_header(headers, "Content-Type"):
            _set_header(headers, "Content-Type", DEFAULT_CONTENT_TYPE[body_type])

    timeout_raw = spec.get("timeout", 30)
    try:
        timeout = float(timeout_raw if timeout_raw not in (None, "") else 30)
    except (TypeError, ValueError):
        raise ValueError(f"timeout 必须是数字: {timeout_raw!r}") from None
    if timeout <= 0:
        raise ValueError("timeout 必须大于 0")

    proxy = _resolve_vars(str(spec.get("proxy") or "").strip(), var_map)
    verify_ssl = spec.get("verify_ssl", True)
    if verify_ssl is None:
        verify_ssl = True

    return {
        "method": method,
        "url": url,
        "headers": headers,
        "body": body,
        "body_type": body_type,
        "params": [list(pair) for pair in params],
        "sign": sign_result,
        "environment": (env_config or {}).get("alias", "") if environment else "",
        "verify_ssl": bool(verify_ssl),
        "timeout": timeout,
        "proxy": proxy,
        "follow_redirects": bool(spec.get("follow_redirects", True)),
    }