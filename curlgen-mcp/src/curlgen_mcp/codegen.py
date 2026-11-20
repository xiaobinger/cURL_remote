"""Generate request code in ten languages.

Static mode embeds the already-computed signature. Runtime mode regenerates the
signature in the target language for Python, JavaScript, TypeScript, Node.js
and Java; other languages fall back to static code with a note.

The generated snippets intentionally use only the standard library where
possible (Java ``java.net.http``, C# ``HttpClient``, Go ``net/http``) and keep
the exact request body/headers produced by :mod:`curlgen_mcp.request`.
"""

import json
import pprint
import shlex
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .request import _form_pairs, _json_object, _sign_value

LANGS = (
    "curl",
    "python",
    "javascript",
    "typescript",
    "node",
    "java",
    "csharp",
    "go",
    "php",
    "rust",
)
RUNTIME_LANGS = ("python", "javascript", "typescript", "node", "java")


def _text(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def _py(value):
    return pprint.pformat(value, width=100, sort_dicts=False)


def _json(value, indent=2):
    return json.dumps(value, ensure_ascii=False, indent=indent)


def _json_str(value):
    return json.dumps(str(value), ensure_ascii=False)


def _php_str(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _rust_str(value):
    return json.dumps(str(value), ensure_ascii=False)


def _java_str(value):
    return json.dumps(str(value), ensure_ascii=False)


def _timeout_text(value):
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def _has_header(headers, name):
    return any(key.lower() == name.lower() for key in headers)


def _get_header(headers, name, default=""):
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return default


def _remove_header(headers, name):
    for key in list(headers):
        if key.lower() == name.lower():
            del headers[key]


def strip_signature(req):
    """Return a copy of ``req`` with the injected signature removed."""
    result = dict(req)
    result["headers"] = dict(req.get("headers") or {})
    result["body"] = req.get("body")
    result["sign"] = dict(req.get("sign") or {})
    sign = result["sign"]
    if not sign.get("enabled"):
        return result

    field = sign.get("field", "sign")
    placement = sign.get("placement", "")

    if placement == "query":
        parts = urlsplit(result["url"])
        query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key != field
        ]
        result["url"] = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )
    elif placement == "header":
        _remove_header(result["headers"], sign.get("header", "X-Sign"))
    elif placement == "body":
        body_type = req.get("body_type", "none")
        body = result.get("body") or ""
        if body_type == "json":
            obj = _json_object(body)
            if isinstance(obj, dict):
                obj.pop(field, None)
                result["body"] = _json(obj) if obj else "{}"
        elif body_type in ("form", "raw"):
            kept = []
            for segment in body.split("&"):
                if "=" in segment and segment.split("=", 1)[0] == field:
                    continue
                kept.append(segment)
            result["body"] = "&".join(kept)

    result["sign"] = {
        **sign,
        "enabled": False,
        "value": "",
        "placement": "none",
        "secret": sign.get("secret", ""),
    }
    return result


def _collect_sign_params(req):
    params = {str(key): _text(value) for key, value in req.get("params", [])}
    body_type = req.get("body_type", "none")
    body = req.get("body") or ""
    if body_type == "json" and body:
        obj = _json_object(body)
        if isinstance(obj, dict):
            for key, value in obj.items():
                normalized = _sign_value(value)
                if normalized is not None:
                    params[str(key)] = normalized
    elif body_type in ("form", "raw") and body:
        for key, value in _form_pairs(body, body_type):
            params[key] = value
    return params


def _body_object(req):
    body_type = req.get("body_type", "none")
    if body_type == "json":
        return _json_object(req.get("body") or "")
    return None


def _render_curl(req):
    parts = ["curl"]
    method = req["method"]
    if method != "GET":
        parts.append(f"-X {method}")
    if not req.get("verify_ssl", True):
        parts.append("-k")
    if req.get("follow_redirects", True):
        parts.append("-L")
    if req.get("proxy"):
        parts.append(f"--proxy {shlex.quote(req['proxy'])}")
    parts.append(f"--max-time {_timeout_text(req.get('timeout', 30))}")
    for key, value in req.get("headers", {}).items():
        parts.append(f"-H {shlex.quote(f'{key}: {value}')}")
    if req.get("body") is not None:
        parts.append(f"--data-raw {shlex.quote(req['body'])}")
    parts.append(shlex.quote(req["url"]))
    return " \\\n  ".join(parts)


def _render_python_static(req):
    lines = [
        "import requests",
        "",
        f"url = {_json_str(req['url'])}",
        "headers = " + _py(req.get("headers") or {}),
    ]
    if req.get("body") is not None:
        lines.append(f"body = {_json_str(req['body'])}")
    lines += ["", "response = requests.request("]
    lines.append(f"    {_json_str(req['method'])},")
    lines.append("    url,")
    lines.append("    headers=headers,")
    if req.get("body") is not None:
        lines.append('    data=body.encode("utf-8"),')
    lines.append(f"    timeout={float(req.get('timeout', 30))},")
    if not req.get("verify_ssl", True):
        lines.append("    verify=False,")
    if req.get("proxy"):
        lines.append(f"    proxies={_py({'http': req['proxy'], 'https': req['proxy']})},")
    lines.append(
        f"    allow_redirects={'True' if req.get('follow_redirects', True) else 'False'},"
    )
    lines += [")", "", "print(response.status_code)", "print(response.text)"]
    return "\n".join(lines)


def _render_python_runtime(req):
    unsigned = strip_signature(req)
    sign = req["sign"]
    body_type = unsigned.get("body_type", "none")
    body_obj = _body_object(unsigned)
    sign_params = _collect_sign_params(unsigned)

    lines = [
        "import hashlib",
        "import json",
        "import os",
        "import requests",
        "",
        f"url = {_json_str(unsigned['url'])}",
        "headers = " + _py(unsigned.get("headers") or {}),
        f"SIGN_PARAMS = {_py(sign_params)}",
        'SIGN_KEY = os.environ.get("CURLGEN_SIGN_KEY", "")',
        f"SIGN_FIELD = {_json_str(sign.get('field', 'sign'))}",
        f"SIGN_PLACEMENT = {_json_str(sign.get('placement', 'body'))}",
        f"SIGN_HEADER = {_json_str(sign.get('header', 'X-Sign'))}",
        f"SIGN_ALGO = {_json_str(sign.get('algo', 'MD5'))}",
        "",
        "if not SIGN_KEY:",
        '    raise SystemExit("CURLGEN_SIGN_KEY is required for runtime signing")',
        "",
    ]

    if body_type == "json" and isinstance(body_obj, dict):
        lines.append("body = " + _py(body_obj))
    elif body_type == "json":
        lines.append(f"body = {_json_str(unsigned.get('body') or '')}")
    elif body_type in ("form", "raw"):
        lines.append(f"body = {_json_str(unsigned.get('body') or '')}")
    else:
        lines.append("body = None")

    lines += [
        "",
        "def compute_sign(params, secret, algo, field):",
        "    items = [",
        "        (str(key), value)",
        "        for key, value in params.items()",
        "        if str(key) != field and value is not None and str(value) != \"\"",
        "    ]",
        "    items.sort(key=lambda item: item[0].upper())",
        "    text = \"&\".join(f\"{key}={value}\" for key, value in items) + \"&\"",
        "    payload = (text + f\"key={secret}\").encode(\"utf-8\")",
        "    digest = hashlib.sha256(payload) if algo == \"SHA256\" else hashlib.md5(payload)",
        "    return digest.hexdigest().upper()",
        "",
        "sign = compute_sign(SIGN_PARAMS, SIGN_KEY, SIGN_ALGO, SIGN_FIELD)",
        "",
        "if SIGN_PLACEMENT == \"query\":",
        "    url += (\"&\" if \"?\" in url else \"?\") + f\"{SIGN_FIELD}={sign}\"",
        "elif SIGN_PLACEMENT == \"header\":",
        "    headers[SIGN_HEADER] = sign",
        "elif SIGN_PLACEMENT == \"body\":",
    ]

    if body_type == "json" and isinstance(body_obj, dict):
        lines += [
            "    body[SIGN_FIELD] = sign",
            "    body = json.dumps(body, ensure_ascii=False, indent=2)",
        ]
    elif body_type in ("form", "raw"):
        lines += [
            "    body += (\"&\" if body else \"\") + f\"{SIGN_FIELD}={sign}\"",
        ]
    else:
        lines += [
            "    body = json.dumps({SIGN_FIELD: sign}, ensure_ascii=False, indent=2)",
        ]

    lines += [
        "",
        "response = requests.request(",
        f"    {_json_str(req['method'])},",
        "    url,",
        "    headers=headers,",
        "    data=body.encode(\"utf-8\") if body is not None else None,",
        f"    timeout={float(req.get('timeout', 30))},",
    ]
    if not req.get("verify_ssl", True):
        lines.append("    verify=False,")
    if req.get("proxy"):
        lines.append(f"    proxies={_py({'http': req['proxy'], 'https': req['proxy']})},")
    lines.append(
        f"    allow_redirects={'True' if req.get('follow_redirects', True) else 'False'},"
    )
    lines += [
        ")",
        "print(response.status_code)",
        "print(response.text)",
    ]
    return "\n".join(lines)


def _js_headers(headers):
    return _json(headers, indent=2)


def _render_javascript_static(req):
    lines = [
        "async function main() {",
        "  const response = await fetch(" + _json_str(req["url"]) + ", {",
        f"    method: {_json_str(req['method'])},",
        "    headers: " + _js_headers(req.get("headers") or {}) + ",",
    ]
    if req.get("body") is not None:
        lines.append("    body: " + _json_str(req["body"]) + ",")
    lines += [
        f"    redirect: {'\"follow\"' if req.get('follow_redirects', True) else '\"manual\"'},",
        "  });",
        "  console.log(response.status);",
        "  console.log(await response.text());",
        "}",
        "",
        "main().catch((error) => {",
        "  console.error(error);",
        "  process.exit(1);",
        "});",
    ]
    return "\n".join(lines)


def _js_compute_sign():
    return [
        "function computeSign(params, secret, algo, field) {",
        "  const entries = Object.entries(params)",
        "    .filter(([key, value]) => key !== field && value !== null && value !== undefined && String(value) !== \"\")",
        "    .sort(([a], [b]) => {",
        "      const left = a.toUpperCase();",
        "      const right = b.toUpperCase();",
        "      return left < right ? -1 : left > right ? 1 : 0;",
        "    });",
        "  const text = entries.map(([key, value]) => `${key}=${value}`).join(\"&\") + \"&\";",
        "  return crypto.createHash(algo === \"SHA256\" ? \"sha256\" : \"md5\")",
        "    .update(text + \"key=\" + secret, \"utf8\")",
        "    .digest(\"hex\")",
        "    .toUpperCase();",
        "}",
    ]


def _render_javascript_runtime(req, typescript=False):
    unsigned = strip_signature(req)
    sign = req["sign"]
    body_type = unsigned.get("body_type", "none")
    body_obj = _body_object(unsigned)
    sign_params = _collect_sign_params(unsigned)

    lines = []
    if typescript:
        lines += [
            "import * as crypto from \"crypto\";",
            "",
            "type SignValue = string | number | boolean | null;",
            "const SIGN_PARAMS: Record<string, SignValue> = " + _json(sign_params, indent=2) + ";",
        ]
    else:
        lines += [
            "const crypto = require(\"crypto\");",
            "",
            "const SIGN_PARAMS = " + _json(sign_params, indent=2) + ";",
        ]
    lines += [
        'const SIGN_KEY = process.env.CURLGEN_SIGN_KEY || "";',
        f"const SIGN_FIELD = {_json_str(sign.get('field', 'sign'))};",
        f"const SIGN_PLACEMENT = {_json_str(sign.get('placement', 'body'))};",
        f"const SIGN_HEADER = {_json_str(sign.get('header', 'X-Sign'))};",
        f"const SIGN_ALGO = {_json_str(sign.get('algo', 'MD5'))};",
        "",
        "if (!SIGN_KEY) {",
        '  throw new Error("CURLGEN_SIGN_KEY is required for runtime signing");',
        "}",
        "",
        f"let url = {_json_str(unsigned['url'])};",
        "const headers" + (": Record<string, string>" if typescript else "") + " = " + _js_headers(unsigned.get("headers") or {}) + ";",
    ]

    if body_type == "json" and isinstance(body_obj, dict):
        lines.append("let body" + (": any" if typescript else "") + " = " + _json(body_obj) + ";")
    elif body_type in ("form", "raw"):
        lines.append("let body" + (": any" if typescript else "") + " = " + _json_str(unsigned.get("body") or "") + ";")
    else:
        lines.append("let body" + (": any" if typescript else "") + " = null;")

    lines += [""]
    lines += _js_compute_sign()
    lines += [
        "",
        "const sign = computeSign(SIGN_PARAMS, SIGN_KEY, SIGN_ALGO, SIGN_FIELD);",
        "",
        "if (SIGN_PLACEMENT === \"query\") {",
        "  url += (url.includes(\"?\") ? \"&\" : \"?\") + SIGN_FIELD + \"=\" + sign;",
        "} else if (SIGN_PLACEMENT === \"header\") {",
        "  headers[SIGN_HEADER] = sign;",
        "} else if (SIGN_PLACEMENT === \"body\") {",
    ]
    if body_type == "json" and isinstance(body_obj, dict):
        lines.append("  body = { ...body, [SIGN_FIELD]: sign };")
    elif body_type in ("form", "raw"):
        lines.append("  body = (body || \"\") + (body ? \"&\" : \"\") + SIGN_FIELD + \"=\" + sign;")
    else:
        lines.append("  body = { [SIGN_FIELD]: sign };")
    lines += [
        "}",
        "",
        "async function main" + (": Promise<void>" if typescript else "") + "() {",
        "  const response = await fetch(url, {",
        f"    method: {_json_str(req['method'])},",
        "    headers,",
        "    body: body === null ? undefined : (typeof body === \"object\" ? JSON.stringify(body) : body),",
        f"    redirect: {'\"follow\"' if req.get('follow_redirects', True) else '\"manual\"'},",
        "  });",
        "  console.log(response.status);",
        "  console.log(await response.text());",
        "}",
        "",
        "main().catch((error) => {",
        "  console.error(error);",
        "  process.exit(1);",
        "});",
    ]
    return "\n".join(lines)


def _render_node_static(req):
    lines = [
        "const axios = require(\"axios\");",
        "",
        "(async () => {",
        "  const response = await axios.request({",
        f"    method: {_json_str(req['method'])},",
        f"    url: {_json_str(req['url'])},",
        "    headers: " + _js_headers(req.get("headers") or {}) + ",",
    ]
    if req.get("body") is not None:
        lines.append("    data: " + _json_str(req["body"]) + ",")
    lines += [
        f"    timeout: {int(float(req.get('timeout', 30)) * 1000)},",
        f"    maxRedirects: {5 if req.get('follow_redirects', True) else 0},",
        "    validateStatus: () => true,",
        "  });",
        "  console.log(response.status, response.data);",
        "})().catch((error) => {",
        "  console.error(error);",
        "  process.exit(1);",
        "});",
    ]
    return "\n".join(lines)


def _render_node_runtime(req):
    unsigned = strip_signature(req)
    sign = req["sign"]
    body_type = unsigned.get("body_type", "none")
    body_obj = _body_object(unsigned)
    sign_params = _collect_sign_params(unsigned)

    lines = [
        "const axios = require(\"axios\");",
        "const crypto = require(\"crypto\");",
        "",
        "const SIGN_PARAMS = " + _json(sign_params) + ";",
        'const SIGN_KEY = process.env.CURLGEN_SIGN_KEY || "";',
        f"const SIGN_FIELD = {_json_str(sign.get('field', 'sign'))};",
        f"const SIGN_PLACEMENT = {_json_str(sign.get('placement', 'body'))};",
        f"const SIGN_HEADER = {_json_str(sign.get('header', 'X-Sign'))};",
        f"const SIGN_ALGO = {_json_str(sign.get('algo', 'MD5'))};",
        "",
        "if (!SIGN_KEY) {",
        '  throw new Error("CURLGEN_SIGN_KEY is required for runtime signing");',
        "}",
        "",
        f"let url = {_json_str(unsigned['url'])};",
        "const headers = " + _js_headers(unsigned.get("headers") or {}) + ";",
    ]
    if body_type == "json" and isinstance(body_obj, dict):
        lines.append("let body = " + _json(body_obj) + ";")
    elif body_type in ("form", "raw"):
        lines.append("let body = " + _json_str(unsigned.get("body") or "") + ";")
    else:
        lines.append("let body = null;")
    lines += [""]
    lines += _js_compute_sign()
    lines += [
        "",
        "const sign = computeSign(SIGN_PARAMS, SIGN_KEY, SIGN_ALGO, SIGN_FIELD);",
        "",
        "if (SIGN_PLACEMENT === \"query\") {",
        "  url += (url.includes(\"?\") ? \"&\" : \"?\") + SIGN_FIELD + \"=\" + sign;",
        "} else if (SIGN_PLACEMENT === \"header\") {",
        "  headers[SIGN_HEADER] = sign;",
        "} else if (SIGN_PLACEMENT === \"body\") {",
    ]
    if body_type == "json" and isinstance(body_obj, dict):
        lines.append("  body = { ...body, [SIGN_FIELD]: sign };")
    elif body_type in ("form", "raw"):
        lines.append("  body = (body || \"\") + (body ? \"&\" : \"\") + SIGN_FIELD + \"=\" + sign;")
    else:
        lines.append("  body = { [SIGN_FIELD]: sign };")
    lines += [
        "}",
        "",
        "(async () => {",
        "  const response = await axios.request({",
        f"    method: {_json_str(req['method'])},",
        "    url,",
        "    headers,",
        "    data: body,",
        f"    timeout: {int(float(req.get('timeout', 30)) * 1000)},",
        f"    maxRedirects: {5 if req.get('follow_redirects', True) else 0},",
        "    validateStatus: () => true,",
        "  });",
        "  console.log(response.status, response.data);",
        "})().catch((error) => {",
        "  console.error(error);",
        "  process.exit(1);",
        "});",
    ]
    return "\n".join(lines)


def _render_java_static(req):
    timeout = _timeout_text(req.get("timeout", 30))
    redirect = "NORMAL" if req.get("follow_redirects", True) else "NEVER"
    lines = [
        "import java.net.URI;",
        "import java.net.http.HttpClient;",
        "import java.net.http.HttpRequest;",
        "import java.net.http.HttpResponse;",
        "import java.time.Duration;",
        "",
        "public class Main {",
        "  public static void main(String[] args) throws Exception {",
        "    HttpClient client = HttpClient.newBuilder()",
        f"        .followRedirects(HttpClient.Redirect.{redirect})",
        f"        .connectTimeout(Duration.ofSeconds({timeout}))",
        "        .build();",
        "    HttpRequest request = HttpRequest.newBuilder()",
        f"        .uri(URI.create({_java_str(req['url'])}))",
        f"        .timeout(Duration.ofSeconds({timeout}))",
    ]
    for key, value in req.get("headers", {}).items():
        lines.append(f"        .header({_java_str(key)}, {_java_str(value)})")
    body_publisher = (
        f"HttpRequest.BodyPublishers.ofString({_java_str(req['body'])})"
        if req.get("body") is not None
        else "HttpRequest.BodyPublishers.noBody()"
    )
    lines += [
        f"        .method({_java_str(req['method'])}, {body_publisher})",
        "        .build();",
        "    HttpResponse<String> response = client.send(",
        "        request, HttpResponse.BodyHandlers.ofString());",
        "    System.out.println(response.statusCode());",
        "    System.out.println(response.body());",
        "  }",
        "}",
    ]
    return "\n".join(lines)


def _render_java_runtime(req):
    unsigned = strip_signature(req)
    sign = req["sign"]
    body_type = unsigned.get("body_type", "none")
    body_obj = _body_object(unsigned)
    sign_params = _collect_sign_params(unsigned)
    timeout = _timeout_text(req.get("timeout", 30))
    redirect = "NORMAL" if req.get("follow_redirects", True) else "NEVER"

    lines = [
        "import java.net.URI;",
        "import java.net.http.HttpClient;",
        "import java.net.http.HttpRequest;",
        "import java.net.http.HttpResponse;",
        "import java.nio.charset.StandardCharsets;",
        "import java.security.MessageDigest;",
        "import java.time.Duration;",
        "import java.util.ArrayList;",
        "import java.util.Comparator;",
        "import java.util.LinkedHashMap;",
        "import java.util.List;",
        "import java.util.Locale;",
        "import java.util.Map;",
        "",
        "public class Main {",
        f"  static final String SIGN_PLACEMENT = {_java_str(sign.get('placement', 'body'))};",
        f"  static final String SIGN_FIELD = {_java_str(sign.get('field', 'sign'))};",
        f"  static final String SIGN_HEADER = {_java_str(sign.get('header', 'X-Sign'))};",
        '  static final String SIGN_KEY = System.getenv().getOrDefault("CURLGEN_SIGN_KEY", "");',
        "",
        "  static String computeSign(Map<String, String> params, String secret, String algo, String field) throws Exception {",
        "    List<Map.Entry<String, String>> entries = new ArrayList<>();",
        "    for (Map.Entry<String, String> entry : params.entrySet()) {",
        "      if (!entry.getKey().equals(field) && entry.getValue() != null && !entry.getValue().isEmpty()) {",
        "        entries.add(entry);",
        "      }",
        "    }",
        "    entries.sort(Comparator.comparing(entry -> entry.getKey().toUpperCase(Locale.ROOT)));",
        "    StringBuilder text = new StringBuilder();",
        "    for (Map.Entry<String, String> entry : entries) {",
        "      text.append(entry.getKey()).append('=').append(entry.getValue()).append('&');",
        "    }",
        "    text.append(\"key=\").append(secret);",
        "    MessageDigest digest = MessageDigest.getInstance(\"SHA256\".equalsIgnoreCase(algo) ? \"SHA-256\" : \"MD5\");",
        "    byte[] bytes = digest.digest(text.toString().getBytes(StandardCharsets.UTF_8));",
        "    StringBuilder hex = new StringBuilder();",
        "    for (byte value : bytes) {",
        "      hex.append(String.format(\"%02X\", value));",
        "    }",
        "    return hex.toString();",
        "  }",
        "",
        "  public static void main(String[] args) throws Exception {",
        "    Map<String, String> signParams = new LinkedHashMap<>();",
    ]
    for key, value in sign_params.items():
        lines.append(f"    signParams.put({_java_str(key)}, {_java_str(_text(value))});")
    lines += [
        "    if (SIGN_KEY.isEmpty()) {",
        '      throw new IllegalStateException("CURLGEN_SIGN_KEY is required for runtime signing");',
        "    }",
        "    String sign = computeSign(signParams, SIGN_KEY, " + _java_str(sign.get('algo', 'MD5')) + ", SIGN_FIELD);",
        f"    String url = {_java_str(unsigned['url'])};",
        "    Map<String, String> headers = new LinkedHashMap<>();",
    ]
    for key, value in unsigned.get("headers", {}).items():
        lines.append(f"    headers.put({_java_str(key)}, {_java_str(value)});")
    if body_type == "json" and isinstance(body_obj, dict):
        lines.append(f"    String body = {_java_str(_json(body_obj))};")
    elif body_type in ("form", "raw"):
        lines.append(f"    String body = {_java_str(unsigned.get('body') or '')};")
    else:
        lines.append("    String body = null;")
    lines += [
        "",
        "    if (\"query\".equals(SIGN_PLACEMENT)) {",
        "      url += (url.contains(\"?\") ? \"&\" : \"?\") + SIGN_FIELD + \"=\" + sign;",
        "    } else if (\"header\".equals(SIGN_PLACEMENT)) {",
        "      headers.put(SIGN_HEADER, sign);",
        "    } else if (\"body\".equals(SIGN_PLACEMENT)) {",
    ]
    if body_type == "json" and isinstance(body_obj, dict):
        lines += [
            "      int index = body.lastIndexOf('}');",
            "      body = body.substring(0, index) + \", \\\"\" + SIGN_FIELD + \"\\\": \\\"\" + sign + \"\\\"\" + body.substring(index);",
        ]
    elif body_type in ("form", "raw"):
        lines += [
            "      body = (body == null || body.isEmpty() ? \"\" : body + \"&\") + SIGN_FIELD + \"=\" + sign;",
        ]
    else:
        lines += [
            "      body = \"{\\\"\" + SIGN_FIELD + \"\\\": \\\"\" + sign + \"\\\"}\";",
        ]
    lines += [
        "    }",
        "",
        "    HttpClient client = HttpClient.newBuilder()",
        f"        .followRedirects(HttpClient.Redirect.{redirect})",
        f"        .connectTimeout(Duration.ofSeconds({timeout}))",
        "        .build();",
        "    HttpRequest.Builder builder = HttpRequest.newBuilder()",
        "        .uri(URI.create(url))",
        f"        .timeout(Duration.ofSeconds({timeout}))",
        f"        .method({_java_str(req['method'])}, HttpRequest.BodyPublishers.ofString(body == null ? \"\" : body));",
        "    for (Map.Entry<String, String> entry : headers.entrySet()) {",
        "      builder.header(entry.getKey(), entry.getValue());",
        "    }",
        "    HttpResponse<String> response = client.send(",
        "        builder.build(), HttpResponse.BodyHandlers.ofString());",
        "    System.out.println(response.statusCode());",
        "    System.out.println(response.body());",
        "  }",
        "}",
    ]
    return "\n".join(lines)


def _render_csharp_static(req):
    timeout = _timeout_text(req.get("timeout", 30))
    headers = dict(req.get("headers") or {})
    content_type = _get_header(headers, "Content-Type")
    lines = [
        "using System;",
        "using System.Net.Http;",
        "using System.Text;",
        "using System.Threading.Tasks;",
        "",
        "class Program {",
        "  static async Task Main() {",
    ]
    if not req.get("verify_ssl", True) or not req.get("follow_redirects", True):
        lines += [
            "    var handler = new HttpClientHandler();",
            f"    handler.AllowAutoRedirect = {'true' if req.get('follow_redirects', True) else 'false'};",
        ]
        if not req.get("verify_ssl", True):
            lines.append(
                "    handler.ServerCertificateCustomValidationCallback = (message, cert, chain, errors) => true;"
            )
        lines.append("    using var client = new HttpClient(handler);")
    else:
        lines.append("    using var client = new HttpClient();")
    lines += [
        f"    client.Timeout = TimeSpan.FromSeconds({timeout});",
        f"    var request = new HttpRequestMessage(new HttpMethod({_json_str(req['method'])}), {_json_str(req['url'])});",
    ]
    for key, value in headers.items():
        if key.lower() == "content-type":
            continue
        lines.append(
            f"    request.Headers.TryAddWithoutValidation({_json_str(key)}, {_json_str(value)});"
        )
    if req.get("body") is not None:
        media_type = content_type or "text/plain"
        lines.append(
            f"    request.Content = new StringContent({_json_str(req['body'])}, Encoding.UTF8, {_json_str(media_type)});"
        )
    lines += [
        "    var response = await client.SendAsync(request);",
        "    Console.WriteLine((int)response.StatusCode);",
        "    Console.WriteLine(await response.Content.ReadAsStringAsync());",
        "  }",
        "}",
    ]
    return "\n".join(lines)


def _render_go_static(req):
    timeout = _timeout_text(req.get("timeout", 30))
    lines = ["package main", "", "import ("]
    if not req.get("verify_ssl", True):
        lines.append('  "crypto/tls"')
    lines += ['  "fmt"', '  "io"', '  "net/http"']
    if req.get("body") is not None:
        lines.append('  "strings"')
    lines += ['  "time"', ")", "", "func main() {"]
    body_expr = (
        f"strings.NewReader({_json_str(req['body'])})"
        if req.get("body") is not None
        else "nil"
    )
    lines.append(
        f"  req, err := http.NewRequest({_json_str(req['method'])}, {_json_str(req['url'])}, {body_expr})"
    )
    lines += [
        "  if err != nil {",
        "    panic(err)",
        "  }",
    ]
    for key, value in req.get("headers", {}).items():
        lines.append(f"  req.Header.Set({_json_str(key)}, {_json_str(value)})")
    client_lines = []
    if not req.get("verify_ssl", True):
        client_lines.append(
            "    Transport: &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}},"
        )
    if not req.get("follow_redirects", True):
        client_lines.append(
            "    CheckRedirect: func(req *http.Request, via []*http.Request) error { return http.ErrUseLastResponse },"
        )
    if req.get("proxy"):
        client_lines.append(f"    // proxy: {req['proxy']}")
    if client_lines:
        lines.append(f"  client := &http.Client{{Timeout: {timeout} * time.Second,")
        lines.extend(client_lines)
        lines.append("  }")
    else:
        lines.append(f"  client := &http.Client{{Timeout: {timeout} * time.Second}}")
    lines += [
        "  resp, err := client.Do(req)",
        "  if err != nil {",
        "    panic(err)",
        "  }",
        "  defer resp.Body.Close()",
        "  fmt.Println(resp.StatusCode)",
        "  body, _ := io.ReadAll(resp.Body)",
        "  fmt.Println(string(body))",
        "}",
    ]
    return "\n".join(lines)


def _render_php_static(req):
    lines = [
        "<?php",
        f"$ch = curl_init({_php_str(req['url'])});",
        "$headers = [",
    ]
    for key, value in req.get("headers", {}).items():
        lines.append(f"    {_php_str(f'{key}: {value}')},")
    lines += [
        "];",
        "curl_setopt_array($ch, [",
        f"    CURLOPT_CUSTOMREQUEST => {_php_str(req['method'])},",
        "    CURLOPT_RETURNTRANSFER => true,",
        "    CURLOPT_HTTPHEADER => $headers,",
        f"    CURLOPT_TIMEOUT => {int(float(req.get('timeout', 30))) if float(req.get('timeout', 30)).is_integer() else float(req.get('timeout', 30))},",
        f"    CURLOPT_FOLLOWLOCATION => {'true' if req.get('follow_redirects', True) else 'false'},",
    ]
    if not req.get("verify_ssl", True):
        lines += [
            "    CURLOPT_SSL_VERIFYPEER => false,",
            "    CURLOPT_SSL_VERIFYHOST => 0,",
        ]
    if req.get("proxy"):
        lines.append(f"    CURLOPT_PROXY => {_php_str(req['proxy'])},")
    if req.get("body") is not None:
        lines.append(f"    CURLOPT_POSTFIELDS => {_php_str(req['body'])},")
    lines += [
        "]);",
        "$response = curl_exec($ch);",
        "$status = curl_getinfo($ch, CURLINFO_HTTP_CODE);",
        "curl_close($ch);",
        "echo $status, \"\\n\", $response;",
    ]
    return "\n".join(lines)


def _render_rust_static(req):
    timeout = _timeout_text(req.get("timeout", 30))
    lines = [
        "use reqwest::blocking::Client;",
        "use std::time::Duration;",
        "",
        "fn main() -> Result<(), Box<dyn std::error::Error>> {",
        "    let mut builder = Client::builder()",
        f"        .timeout(Duration::from_secs({timeout}))",
        "        .redirect(if " + ("true" if req.get("follow_redirects", True) else "false") + " { reqwest::redirect::Policy::limited(10) } else { reqwest::redirect::Policy::none() });",
    ]
    if not req.get("verify_ssl", True):
        lines.append("    builder = builder.danger_accept_invalid_certs(true);")
    lines += [
        "    let client = builder.build()?;",
        f"    let request = client.request(reqwest::Method::from_bytes({_rust_bytes(req['method'])}), {_rust_str(req['url'])})",
    ]
    for key, value in req.get("headers", {}).items():
        lines.append(f"        .header({_rust_str(key)}, {_rust_str(value)})")
    if req.get("body") is not None:
        lines.append(f"        .body({_rust_str(req['body'])})")
    lines += [
        "        .send()?;",
        "    println!(\"{}\", request.status());",
        "    println!(\"{}\", request.text()?);",
        "    Ok(())",
        "}",
    ]
    return "\n".join(lines)


def _rust_bytes(value):
    return "&[" + ", ".join(f"b'{char}'" for char in str(value)) + "]"


def generate_code(req, lang="curl", sign_mode="static"):
    lang = str(lang or "curl").strip().lower()
    if lang not in LANGS:
        raise ValueError(f"不支持的语言: {lang}（可选: {', '.join(LANGS)}）")
    mode = str(sign_mode or "static").strip().lower()
    if mode not in ("static", "runtime"):
        raise ValueError("sign_mode 只能是 static 或 runtime")

    notes = []
    effective = mode
    if mode == "runtime" and lang not in RUNTIME_LANGS:
        effective = "static"
        notes.append(f"{lang} 暂不支持运行时签名，已生成静态签名值")
    if mode == "runtime" and not (req.get("sign") or {}).get("enabled"):
        effective = "static"
        notes.append("签名未启用，已生成不含签名的代码")
    if effective == "runtime":
        notes.append(
            "运行时签名代码从环境变量 CURLGEN_SIGN_KEY 读取密钥，真实密钥不会写入代码"
        )

    if lang == "python":
        code = (
            _render_python_runtime(req)
            if effective == "runtime"
            else _render_python_static(req)
        )
    elif lang == "javascript":
        code = (
            _render_javascript_runtime(req)
            if effective == "runtime"
            else _render_javascript_static(req)
        )
    elif lang == "typescript":
        code = (
            _render_javascript_runtime(req, typescript=True)
            if effective == "runtime"
            else _render_javascript_static(req)
        )
    elif lang == "node":
        code = (
            _render_node_runtime(req)
            if effective == "runtime"
            else _render_node_static(req)
        )
    elif lang == "java":
        code = (
            _render_java_runtime(req)
            if effective == "runtime"
            else _render_java_static(req)
        )
    elif lang == "curl":
        code = _render_curl(req)
    elif lang == "csharp":
        code = _render_csharp_static(req)
    elif lang == "go":
        code = _render_go_static(req)
    elif lang == "php":
        code = _render_php_static(req)
    else:
        code = _render_rust_static(req)

    return {
        "lang": lang,
        "sign_mode": effective,
        "code": code,
        "notes": notes,
    }