"""cURL 命令构建（纯函数版，供新旧 UI 共用）

从 main_window.generate_curl 提取，逻辑保持一致：
环境变量替换 → 签名计算 → 参数拼接 → body 注入签名。
"""
import base64
import json
import logging

from .config import load_env_configs
from .signing import compute_sign

logger = logging.getLogger("curlgen.curl_builder")

NO_BODY_METHODS = ("GET", "HEAD", "OPTIONS")


def resolve_vars(text, var_map):
    """将 {{key}} 替换为环境变量值"""
    if not var_map or not text:
        return text
    for k, v in var_map.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def build_curl(data):
    """根据请求数据 dict 生成 cURL 命令。

    data: {method, url, env, headers:[[k,v]...], params, body_type, body,
           auth_type, auth_value/auth_user+auth_pass/auth_header_name,
           ssl, silent, proxy, timeout}
    返回 (curl_cmd, sign_preview)
    """
    parts = ["curl"]
    method = data.get("method", "GET")
    url = (data.get("url") or "").strip()

    if not url:
        raise ValueError("请输入 URL")

    # ── 环境配置 & 变量映射 ──
    var_map = {}
    env_cfg = None
    selected_env = data.get("env", "")
    if selected_env and selected_env != "(不选择环境)":
        configs = load_env_configs()
        env_cfg = next((c for c in configs if c["alias"] == selected_env), None)
    if env_cfg:
        for ck, cv in env_cfg.get("custom_params", []):
            if ck:
                var_map[ck] = cv

    url = resolve_vars(url, var_map)

    if method != "GET":
        parts.append(f"-X {method}")

    if data.get("ssl"):
        parts.append("-k")
    if data.get("silent"):
        parts.append("-s")

    proxy = resolve_vars((data.get("proxy") or "").strip(), var_map)
    if proxy:
        parts.append(f'-x "{proxy}"')

    timeout = (data.get("timeout") or "").strip()
    if timeout:
        parts.append(f"--max-time {timeout}")

    # Auth
    auth_type = data.get("auth_type", "None")
    if auth_type == "Bearer Token":
        token = resolve_vars((data.get("auth_value") or "").strip(), var_map)
        if token:
            parts.append(f'-H "Authorization: Bearer {token}"')
    elif auth_type == "Basic Auth":
        u = resolve_vars((data.get("auth_user") or "").strip(), var_map)
        p = resolve_vars((data.get("auth_pass") or "").strip(), var_map)
        if u:
            creds = base64.b64encode(f"{u}:{p}".encode()).decode()
            parts.append(f'-H "Authorization: Basic {creds}"')
    elif auth_type == "API Key":
        h = (data.get("auth_header_name") or "X-API-Key").strip() or "X-API-Key"
        v = resolve_vars((data.get("auth_value") or "").strip(), var_map)
        if v:
            parts.append(f'-H "{h}: {v}"')

    # Headers
    for k, v in data.get("headers", []):
        k = resolve_vars(str(k), var_map)
        v = resolve_vars(str(v), var_map)
        parts.append(f'-H "{k}: {v}"')

    # Query params
    params = [(resolve_vars(str(k), var_map), resolve_vars(str(v), var_map))
              for k, v in data.get("params", [])]

    # ── 签名计算 ──
    sign_value = None
    sign_param_name = "sign"
    sign_preview = ""

    if env_cfg:
        if env_cfg.get("enable_sign", True):
            secret_key = env_cfg.get("key", "")
            sign_algo = env_cfg.get("algo", "MD5")
            sign_param_name = env_cfg.get("param_name", "sign")
            all_params = dict(params)
            body_type = data.get("body_type", "JSON")
            if method not in NO_BODY_METHODS and body_type != "None":
                body_text = resolve_vars((data.get("body") or "").strip(), var_map)
                if body_text:
                    if body_type == "JSON":
                        try:
                            obj = json.loads(body_text)
                        except json.JSONDecodeError:
                            obj = None
                        if isinstance(obj, dict):
                            for k, v in obj.items():
                                if isinstance(v, bool):
                                    all_params[str(k)] = "true" if v else "false"
                                elif isinstance(v, (dict, list)):
                                    all_params[str(k)] = json.dumps(
                                        v, separators=(",", ":"), ensure_ascii=False)
                                elif v is not None:
                                    all_params[str(k)] = v
                        else:
                            logger.debug("JSON body 非对象或解析失败，不并入签名: %s", body_text)
                    elif body_type == "Form Data":
                        for line in body_text.splitlines():
                            line = line.strip()
                            if "=" in line:
                                k, v = line.split("=", 1)
                                all_params[k.strip()] = v.strip()
                    else:  # Raw Text
                        for seg in body_text.splitlines():
                            for pair in seg.split("&"):
                                pair = pair.strip()
                                if "=" in pair:
                                    k, v = pair.split("=", 1)
                                    all_params[k.strip()] = v.strip()
            sign_value = compute_sign(all_params, secret_key, sign_algo, sign_param_name)
            if sign_value:
                sign_preview = f"签名: {sign_value}"
                logger.info("签名参数(all_params, dict): %s, 签名值: %s", all_params, sign_value)
        else:
            sign_preview = "(签名已禁用)"

    # 拼接 URL 参数
    if params:
        param_str = "&".join(f"{k}={v}" for k, v in params)
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{param_str}"

    # GET/HEAD/OPTIONS 没有 body，签名放到 URL 参数里
    if sign_value and method in NO_BODY_METHODS:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{sign_param_name}={sign_value}"

    # Body
    body_type = data.get("body_type", "JSON")
    if body_type != "None" and method not in NO_BODY_METHODS:
        body = resolve_vars((data.get("body") or "").strip(), var_map)
        if body:
            if body_type == "JSON":
                parts.append('-H "Content-Type: application/json"')
                try:
                    obj = json.loads(body)
                    if sign_value:
                        obj[sign_param_name] = sign_value
                    body = json.dumps(obj, indent=2, ensure_ascii=False)
                except json.JSONDecodeError:
                    if sign_value and body.rstrip().endswith("}"):
                        insert = f', "{sign_param_name}": "{sign_value}"'
                        body = body.rstrip()[:-1] + insert + "\n}"
                parts.append(f"-d '{body}'")
            elif body_type == "Form Data":
                form_lines = body.strip().splitlines()
                if sign_value:
                    form_lines.append(f"{sign_param_name}={sign_value}")
                for line in form_lines:
                    line = line.strip()
                    if "=" in line:
                        k, v = line.split("=", 1)
                        parts.append(f'-d "{k.strip()}={v.strip()}"')
            else:
                if sign_value:
                    body += f"&{sign_param_name}={sign_value}"
                parts.append(f"-d '{body}'")
        else:
            # body 为空但有签名时，构造 body
            if sign_value:
                if body_type == "JSON":
                    parts.append('-H "Content-Type: application/json"')
                    obj = {sign_param_name: sign_value}
                    parts.append(f"-d '{json.dumps(obj)}'")
                elif body_type == "Form Data":
                    parts.append(f'-d "{sign_param_name}={sign_value}"')
                else:
                    parts.append(f"-d \'{sign_param_name}={sign_value}\'")

    parts.append(f'"{url}"')

    return " \\\n  ".join(parts), sign_preview
