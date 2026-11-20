import base64
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from curlgen_mcp.http import (
    HTTPFailure,
    build_remote_curl_script,
    parse_remote_headers,
    parse_remote_output,
    perform_http,
)
from curlgen_mcp.request import build_request


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/redirect"):
            self._send(302, b"", {"Location": "/echo"})
        elif self.path.startswith("/missing"):
            self._send(404, b"not found", {"Content-Type": "text/plain"})
        elif self.path.startswith("/big"):
            self._send(200, b"x" * 500, {"Content-Type": "text/plain"})
        elif self.path.startswith("/slow"):
            import time

            time.sleep(0.3)
            self._send(200, b"slow", {"Content-Type": "text/plain"})
        else:
            body = json.dumps(
                {
                    "method": self.command,
                    "path": self.path,
                    "accept": self.headers.get("X-Test", ""),
                }
            ).encode()
            self._send(200, body, {"Content-Type": "application/json"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        response = json.dumps(
            {
                "method": self.command,
                "path": self.path,
                "body": body.decode(),
                "content_type": self.headers.get("Content-Type", ""),
            }
        ).encode()
        self._send(200, response, {"Content-Type": "application/json"})


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_local_get_returns_status_headers_and_body(self):
        req = build_request(
            {
                "method": "GET",
                "url": self.base + "/echo",
                "headers": {"X-Test": "yes"},
                "params": {"a": "1"},
            },
            env_config={},
        )
        response = perform_http(req)
        self.assertEqual(response["status"], 200)
        self.assertIn("application/json", response["headers"]["Content-Type"])
        payload = json.loads(response["body"])
        self.assertEqual(payload["method"], "GET")
        self.assertIn("a=1", payload["path"])
        self.assertEqual(payload["accept"], "yes")

    def test_local_post_json_body(self):
        req = build_request(
            {
                "method": "POST",
                "url": self.base + "/post",
                "body_type": "json",
                "body": {"hello": "世界"},
            },
            env_config={},
        )
        response = perform_http(req)
        self.assertEqual(response["status"], 200)
        payload = json.loads(response["body"])
        self.assertEqual(payload["method"], "POST")
        self.assertIn("世界", payload["body"])
        self.assertEqual(payload["content_type"], "application/json")

    def test_http_error_status_is_returned_not_raised(self):
        req = build_request(
            {"method": "GET", "url": self.base + "/missing"},
            env_config={},
        )
        response = perform_http(req)
        self.assertEqual(response["status"], 404)
        self.assertEqual(response["body"], "not found")

    def test_redirect_is_not_followed_when_disabled(self):
        req = build_request(
            {
                "method": "GET",
                "url": self.base + "/redirect",
                "follow_redirects": False,
            },
            env_config={},
        )
        response = perform_http(req)
        self.assertEqual(response["status"], 302)
        self.assertIn("Location", response["headers"])

    def test_response_is_truncated_at_limit(self):
        req = build_request(
            {"method": "GET", "url": self.base + "/big"},
            env_config={},
        )
        with patch.dict(os.environ, {"CURLGEN_MCP_MAX_RESPONSE": "10"}):
            response = perform_http(req)
        self.assertEqual(len(response["body"].encode()), 10)
        self.assertTrue(response["truncated"])
        self.assertEqual(response["size"], 500)

    def test_parse_remote_headers_uses_last_status_block_and_keeps_duplicates(self):
        text = (
            "HTTP/1.1 302 Found\r\nLocation: /next\r\n\r\n"
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            "Set-Cookie: a=1\r\nSet-Cookie: b=2\r\n\r\n"
        )
        parsed = parse_remote_headers(text)
        self.assertEqual(parsed["status"], 200)
        self.assertEqual(parsed["reason"], "OK")
        cookies = [value for key, value in parsed["headers"] if key.lower() == "set-cookie"]
        self.assertEqual(cookies, ["a=1", "b=2"])

    def test_parse_remote_output(self):
        headers = (
            "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
            "Set-Cookie: a=1\r\nSet-Cookie: b=2\r\n\r\n"
        )
        output = (
            "__CG_HTTP_STATS__200|0.12|5|https://example.com/final\n"
            "__CG_HTTP_HEADERS__\n"
            + base64.b64encode(headers.encode()).decode()
            + "\n__CG_HTTP_BODY__\n"
            + base64.b64encode(b"hello").decode()
            + "\n"
        )
        response = parse_remote_output(output)
        self.assertEqual(response["status"], 200)
        self.assertEqual(response["body"], "hello")
        self.assertEqual(response["size"], 5)
        self.assertEqual(response["elapsed_ms"], 120)
        self.assertEqual(response["url"], "https://example.com/final")
        self.assertEqual(response["headers"]["Content-Type"], "text/plain")

    def test_parse_remote_output_without_markers_raises(self):
        with self.assertRaises(HTTPFailure):
            parse_remote_output("curl: (7) connection refused")

    def test_remote_curl_script_contains_method_headers_and_limits(self):
        req = build_request(
            {
                "method": "POST",
                "url": "https://api.example.com/pay",
                "headers": {"X-Test": "yes"},
                "body_type": "json",
                "body": {"a": 1},
                "verify_ssl": False,
                "follow_redirects": False,
                "proxy": "http://proxy:8080",
                "timeout": 12,
            },
            env_config={},
        )
        script = build_remote_curl_script(req, max_bytes=1234)
        self.assertIn("-X POST", script)
        self.assertIn("--max-time 12", script)
        self.assertIn("--max-filesize 1234", script)
        self.assertIn("-k", script)
        self.assertIn("X-Test: yes", script)
        self.assertIn("--proxy http://proxy:8080", script)
        self.assertNotIn("-L", script)
        self.assertIn("__CG_HTTP_HEADERS__", script)
        self.assertIn("__CG_HTTP_BODY__", script)


if __name__ == "__main__":
    unittest.main()