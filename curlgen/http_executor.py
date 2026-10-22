"""本地直接 HTTP 执行器（不再 shell 调 curl）

消费 curl_builder.resolve_request 的结构化结果，用 urllib 直接发请求：
- 自动跟随重定向（记录最终 URL）
- 支持代理、跳过证书校验（对应 curl -k）、超时、手动停止
- 流式读取响应体，通过 on_progress 回调实时输出
- 通过 on_event 回调推送结构化事件（http_req / http_resp），供 Console 展示真实收发报文
"""
import gzip
import logging
import ssl
import threading
import time
import urllib.error
import urllib.request

from .curl_builder import DEFAULT_CONTENT_TYPE
from .ssh_executor import ExecutionError, ExecutionTimeout, ExecutorStop

logger = logging.getLogger("curlgen.http_executor")

# 读取块大小
CHUNK = 16384
# urllib 默认 User-Agent 是 Python-urllib/x.y，换成更中性的
USER_AGENT = "curlgen/2.0"


class DirectHttpExecutor:
    """直接发 HTTP 请求，接口与 LocalExecutor 对齐：run() / stop()"""

    def __init__(self, on_progress=None, on_event=None):
        self.on_progress = on_progress
        self.on_event = on_event
        self._stop_flag = threading.Event()
        self._resp = None

    def stop(self):
        self._stop_flag.set()
        resp = self._resp
        if resp is not None:
            try:
                resp.close()
                logger.info("已断开本地 HTTP 连接")
            except Exception as e:
                logger.warning("断开连接失败: %s", e)

    def _report(self, tag, text):
        if self.on_progress:
            try:
                self.on_progress(tag, text)
            except Exception as e:
                logger.warning("on_progress 回调异常: %s", e)

    def _emit(self, ev):
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception as e:
                logger.warning("on_event 回调异常: %s", e)

    def run(self, req, timeout=30):
        """req: resolve_request 的结果。返回 {"exit_code": 0}（传输成功即 0，HTTP 4xx/5xx 不算失败）"""
        self._stop_flag.clear()
        method = req["method"]
        url = req["url"]
        body = req["body"]
        body_bytes = body.encode("utf-8") if body is not None else None

        # 组请求头：鉴权 + 用户头，缺 Content-Type 时按 body 类型补默认（对齐 curl -d 行为）
        header_pairs = [[k, v] for k, v in req["headers"]]
        if body_bytes is not None and not any(k.lower() == "content-type" for k, _ in header_pairs):
            header_pairs.append(["Content-Type", DEFAULT_CONTENT_TYPE.get(req["body_type"], "application/octet-stream")])
        if not any(k.lower() == "user-agent" for k, _ in header_pairs):
            header_pairs.append(["User-Agent", USER_AGENT])
        headers = {k: v for k, v in header_pairs}

        logger.info("本地直发 %s %s (timeout=%ss)", method, url, timeout)
        self._emit({
            "type": "http_req",
            "method": method,
            "url": url,
            "headers": header_pairs,
            "body": body or "",
        })

        handlers = []
        if req["proxy"]:
            handlers.append(urllib.request.ProxyHandler({"http": req["proxy"], "https": req["proxy"]}))
        if not req["verify_ssl"]:
            # 用户显式勾选「跳过证书校验」（对应 curl -k）才会走到这里，按请求粒度生效
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE  # noqa: S323 - 用户主动选择的调试选项
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        opener = urllib.request.build_opener(*handlers)

        http_req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        t0 = time.monotonic()
        try:
            try:
                resp = opener.open(http_req, timeout=timeout)
            except urllib.error.HTTPError as e:
                resp = e  # 4xx/5xx 也是有效响应
            except TimeoutError as e:  # 含 socket.timeout（3.10+ 为同一类）
                raise ExecutionTimeout(f"请求超时 ({timeout}s)") from e
            except urllib.error.URLError as e:
                reason = getattr(e, "reason", e)
                if isinstance(reason, (TimeoutError,)) or "timed out" in str(reason):
                    raise ExecutionTimeout(f"请求超时 ({timeout}s)") from e
                raise ExecutionError(f"连接失败: {reason}") from e

            self._resp = resp
            try:
                return self._read_response(resp, t0)
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
                self._resp = None
        except (ExecutorStop, ExecutionTimeout, ExecutionError):
            raise
        except Exception as e:
            if self._stop_flag.is_set():
                raise ExecutorStop("已手动停止") from e
            logger.exception("本地直发异常")
            raise ExecutionError(f"请求异常: {e}") from e

    def _read_response(self, resp, t0):
        status = getattr(resp, "status", None) or getattr(resp, "code", 0) or 0
        reason = getattr(resp, "reason", "") or ""
        resp_headers = [[k, v] for k, v in resp.headers.items()]
        final_url = resp.geturl()

        # 解压 / 解码
        raw = resp.read(1)  # 先试探是否有内容
        chunks_raw = []
        while raw:
            if self._stop_flag.is_set():
                raise ExecutorStop("已手动停止")
            chunks_raw.append(raw)
            raw = resp.read(CHUNK)
        payload = b"".join(chunks_raw)
        if self._stop_flag.is_set():
            raise ExecutorStop("已手动停止")

        if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
            try:
                payload = gzip.decompress(payload)
            except Exception as e:
                logger.warning("gzip 解压失败，按原文展示: %s", e)

        charset = resp.headers.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace")
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # 流式输出响应体（按行切片，避免单事件过大）
        for line in text.splitlines(keepends=True):
            if self._stop_flag.is_set():
                raise ExecutorStop("已手动停止")
            self._report("stdout", line)
        if text and not text.endswith("\n"):
            pass  # 最后一行已输出

        self._emit({
            "type": "http_resp",
            "status": status,
            "reason": str(reason),
            "headers": resp_headers,
            "elapsed_ms": elapsed_ms,
            "size": len(payload),
            "url": final_url,
        })
        logger.info("本地直发结束: HTTP %s %s, %dB, %dms", status, reason, len(payload), elapsed_ms)
        return {"exit_code": 0, "stdout": text, "stderr": ""}
