"""HTTP execution (local urllib and remote curl) and response parsing.

All request preparation/signing happens locally in :mod:`curlgen_mcp.request`; the
remote path only replays the resolved method, headers, body and URL via ``curl``
on the target machine, so preview, local execution and remote execution agree.
"""

import base64
import json
import re
import shlex
import ssl
import time
import urllib.error
import urllib.request

from . import paths


class HTTPFailure(Exception):
    """Transport failure (message is safe to show to the user)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _timeout_text(value):
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def _header_dict(pairs):
    result = {}
    for key, value in pairs:
        result[key] = value
    return result


def _declared_size(headers, actual):
    raw = next(
        (value for key, value in headers if key.lower() == "content-length"),
        "",
    )
    try:
        size = int(raw)
    except (TypeError, ValueError):
        return actual
    return max(size, actual)


def perform_http(req, max_bytes=None):
    """Execute a resolved request locally with urllib."""
    limit = paths.max_response_bytes() if max_bytes is None else int(max_bytes)
    method = req["method"]
    body = req.get("body")
    data = body.encode("utf-8") if body is not None else None
    headers = dict(req.get("headers") or {})
    request_obj = urllib.request.Request(
        req["url"], data=data, headers=headers, method=method
    )

    handlers = []
    proxy = req.get("proxy")
    if proxy:
        handlers.append(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    if not req.get("verify_ssl", True):
        context = ssl._create_unverified_context()
        handlers.append(urllib.request.HTTPSHandler(context=context))
    if not req.get("follow_redirects", True):
        handlers.append(_NoRedirect())

    opener = urllib.request.build_opener(*handlers)
    started = time.perf_counter()
    try:
        response = opener.open(request_obj, timeout=float(req.get("timeout", 30)))
    except urllib.error.HTTPError as exc:
        response = exc
    except urllib.error.URLError as exc:
        raise HTTPFailure(f"HTTP 请求失败: {exc.reason}") from exc
    except Exception as exc:
        raise HTTPFailure(f"HTTP 请求失败: {exc}") from exc

    try:
        raw = response.read(limit + 1)
        status = int(getattr(response, "status", None) or response.code)
        reason = getattr(response, "reason", "") or ""
        final_url = response.geturl()
        header_pairs = list(response.headers.items())
    finally:
        try:
            response.close()
        except Exception:
            pass

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    size = _declared_size(header_pairs, len(raw))
    truncated = len(raw) > limit or size > limit
    raw = raw[:limit]
    return {
        "status": status,
        "reason": str(reason),
        "headers": _header_dict(header_pairs),
        "headers_list": [list(pair) for pair in header_pairs],
        "body": raw.decode("utf-8", "replace"),
        "size": size,
        "truncated": truncated,
        "elapsed_ms": elapsed_ms,
        "url": final_url,
        "transport": "urllib",
    }


def build_remote_curl_script(req, max_bytes=None):
    """Build the shell script that performs the request with remote curl.

    Headers and body are written to temporary files and emitted as base64, so
    binary bodies and multi-block redirect headers survive the interactive shell.
    """
    limit = paths.max_response_bytes() if max_bytes is None else int(max_bytes)
    timeout = _timeout_text(req.get("timeout", 30))
    method = req["method"]
    parts = [
        "d=$(mktemp -d)",
        "curl -sS",
        f"--connect-timeout {min(float(timeout), 10) if float(timeout) < 10 else 10}",
        f"--max-time {timeout}",
        f"--max-filesize {limit}",
        '-D "$d/h"',
        '-o "$d/b"',
    ]
    if method == "HEAD":
        parts.append("-I")
    elif method != "GET":
        parts.append(f"-X {method}")
    if not req.get("verify_ssl", True):
        parts.append("-k")
    if req.get("follow_redirects", True):
        parts.append("-L")
    if req.get("proxy"):
        parts.append(f"--proxy {shlex.quote(req['proxy'])}")
    for key, value in (req.get("headers") or {}).items():
        parts.append(f"-H {shlex.quote(f'{key}: {value}')}")
    if req.get("body") is not None:
        parts.append(f"--data-raw {shlex.quote(req['body'])}")
    parts.append(
        "-w '__CG_HTTP_STATS__%{http_code}|%{time_total}|%{size_download}|%{url_effective}\\n'"
    )
    parts.append(shlex.quote(req["url"]))
    script = " ".join(parts)
    script += "; ec=$?"
    script += '; echo "__CG_HTTP_HEADERS__"; base64 "$d/h"'
    script += '; echo "__CG_HTTP_BODY__"; base64 "$d/b"'
    script += '; rm -rf "$d"; (exit $ec)'
    return script


_STATUS_RE = re.compile(r"^HTTP/\S+\s+(\d{3})\s*(.*)$")
_STATS_RE = re.compile(
    r"__CG_HTTP_STATS__(\d{3})\|([0-9.]+)\|(\d+)\|(.*)"
)
_HEADERS_MARK = re.compile(r"(?m)^__CG_HTTP_HEADERS__[ \t]*$")
_BODY_MARK = re.compile(r"(?m)^__CG_HTTP_BODY__[ \t]*$")


def parse_remote_headers(text):
    """Parse the *last* HTTP status block from a curl ``-D`` header dump."""
    blocks = []
    current = None
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        match = _STATUS_RE.match(line.strip())
        if match:
            if current is not None:
                blocks.append(current)
            current = {
                "status": int(match.group(1)),
                "reason": match.group(2).strip(),
                "headers": [],
            }
            continue
        if current is None or not line.strip():
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            current["headers"].append([key.strip(), value.strip()])
    if current is not None:
        blocks.append(current)
    if not blocks:
        raise HTTPFailure("远端响应中没有 HTTP 状态行")
    return blocks[-1]


def parse_remote_output(output, max_bytes=None):
    """Parse the marker/base64 output produced by :func:`build_remote_curl_script`."""
    limit = paths.max_response_bytes() if max_bytes is None else int(max_bytes)
    stats_match = _STATS_RE.search(output or "")
    if not stats_match:
        tail = " ".join((output or "").split())[-300:]
        raise HTTPFailure(f"远端 curl 未返回状态信息: {tail}")
    headers_mark = _HEADERS_MARK.search(output)
    body_mark = _BODY_MARK.search(output)
    if not headers_mark or not body_mark or body_mark.start() < headers_mark.end():
        raise HTTPFailure("远端响应缺少 headers/body 标记")

    headers_b64 = output[headers_mark.end():body_mark.start()]
    body_end = stats_match.start() if stats_match.start() > body_mark.end() else len(output)
    body_b64 = output[body_mark.end():body_end]
    try:
        headers_text = base64.b64decode(
            "".join(headers_b64.split()), validate=True
        ).decode("utf-8", "replace")
        raw = base64.b64decode("".join(body_b64.split()), validate=True)
    except Exception:
        raise HTTPFailure("远端响应 base64 无法解析") from None

    parsed = parse_remote_headers(headers_text)
    size = int(stats_match.group(3))
    if len(raw) > size:
        size = len(raw)
    truncated = len(raw) > limit
    raw = raw[:limit]
    return {
        "status": parsed["status"],
        "reason": parsed["reason"],
        "headers": _header_dict(parsed["headers"]),
        "headers_list": parsed["headers"],
        "body": raw.decode("utf-8", "replace"),
        "size": size,
        "truncated": truncated,
        "elapsed_ms": int(float(stats_match.group(2)) * 1000),
        "url": stats_match.group(4).strip(),
        "transport": "remote-curl",
    }


def pretty_json_body(text):
    try:
        return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return text