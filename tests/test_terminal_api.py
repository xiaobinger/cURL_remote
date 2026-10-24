import http.client
import json
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from curlgen import web_ui
from curlgen.web_ui import WebCurlGenerator, _ApiHandler


_DEFAULT_TOKEN = object()


class FakeTerminals:
    def __init__(self):
        self.calls = []
        self.open_extra = {}
        self.event_response = {
            "session": {"id": "t1", "kind": "local", "label": "", "status": "ready"},
            "events": [{"seq": 1, "type": "stdout", "text": "ready"}],
            "next": 2,
        }

    def open(self, kind="local", label=None):
        self.calls.append(("open", kind, label))
        response = {"id": "t1", "kind": kind, "label": label, "status": "connecting"}
        response.update(self.open_extra)
        return response

    def write(self, session_id, text):
        self.calls.append(("write", session_id, text))

    def events(self, session_id, since=0):
        self.calls.append(("events", session_id, since))
        return self.event_response

    def interrupt(self, session_id):
        self.calls.append(("interrupt", session_id))

    def close(self, session_id):
        self.calls.append(("close", session_id))

    def close_all(self):
        self.calls.append(("close_all",))


class TerminalAPIHelperTests(unittest.TestCase):
    def test_rejects_missing_or_wrong_token(self):
        self.assertFalse(web_ui.terminal_token_valid("expected", None))
        self.assertFalse(web_ui.terminal_token_valid("expected", "wrong"))
        self.assertFalse(web_ui.terminal_token_valid("expected", ""))
        self.assertFalse(web_ui.terminal_token_valid("", "expected"))
        self.assertTrue(web_ui.terminal_token_valid("expected", "expected"))

    def test_open_response_contains_only_public_session_fields(self):
        handler = _ApiHandler.__new__(_ApiHandler)
        handler.app = SimpleNamespace(terminals=FakeTerminals())

        response = handler._terminal_open({"kind": "ssh", "label": "生产A"})

        self.assertEqual(
            response,
            {"id": "t1", "kind": "ssh", "label": "生产A", "status": "connecting"},
        )
        self.assertNotIn("secret", str(response))
        self.assertNotIn("jump_pass", str(response))


class SSHExecutionEventTests(unittest.TestCase):
    def test_metadata_with_trailing_newline_emits_http_response_not_plain_output(self):
        app = WebCurlGenerator.__new__(WebCurlGenerator)
        app.events = []
        app._exec_mode = "ssh"
        app._ssh_hold = ""

        app._on_exec_progress(
            "stdout",
            "response body\n__CG_META__:200:0.125:577:text/html\n",
        )
        app._flush_ssh_hold()

        responses = [event for event in app.events if event["type"] == "http_resp"]
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["status"], 200)
        self.assertEqual(responses[0]["elapsed_ms"], 125)
        output = "".join(
            event["text"] for event in app.events if event["type"] == "output"
        )
        self.assertIn("response body", output)
        self.assertNotIn("__CG_META__", output)


class TerminalAPIHTTPTests(unittest.TestCase):
    TOKEN = "test-terminal-token"

    def setUp(self):
        self.terminals = FakeTerminals()
        self.app = SimpleNamespace(
            port=0,
            terminal_token=self.TOKEN,
            terminals=self.terminals,
            history=[],
        )
        handler_class = type(
            "TerminalAPITestHandler", (_ApiHandler,), {"app": self.app}
        )
        self.server = web_ui.ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
        self.server.daemon_threads = True
        self.app.port = self.server.server_address[1]
        self.server_thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(2)
        self.assertFalse(self.server_thread.is_alive())

    def request(self, method, path, payload=None, token=_DEFAULT_TOKEN):
        if token is _DEFAULT_TOKEN:
            token = self.TOKEN
        headers = {}
        if token:
            headers["X-CurlGen-Token"] = token
        body = payload
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=2)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            response_body = response.read()
            return response.status, response.getheaders(), response_body
        finally:
            connection.close()

    def assert_json_response(
        self, method, path, payload=None, token=_DEFAULT_TOKEN
    ):
        status, headers, body = self.request(method, path, payload, token)
        return status, dict(headers), json.loads(body.decode("utf-8")), body

    def test_every_terminal_route_rejects_bad_token_before_business_work(self):
        requests = [
            ("GET", "/api/terminal/events?id=t1&since=invalid", None),
            ("POST", "/api/terminal/open", b'{"kind":'),
            ("POST", "/api/terminal/input", {"id": "t1", "text": "whoami"}),
            ("POST", "/api/terminal/interrupt", {"id": "t1"}),
            ("POST", "/api/terminal/close", {"id": "t1"}),
            ("GET", "/api/terminal/unknown", None),
            ("POST", "/api/terminal/unknown", {}),
        ]
        for method, path, payload in requests:
            for token in (None, "wrong"):
                with self.subTest(method=method, path=path, token=token):
                    self.terminals.calls.clear()
                    status, _, response, body = self.assert_json_response(
                        method, path, payload, token=token
                    )
                    self.assertEqual(status, 403)
                    self.assertEqual(response, {"error": "forbidden"})
                    self.assertEqual(self.terminals.calls, [])
                    self.assertNotIn(b"secret", body)

    def test_non_ascii_token_is_forbidden_for_get_and_post(self):
        requests = [
            ("GET", "/api/terminal/events?id=t1", None),
            ("POST", "/api/terminal/open", {}),
        ]
        for method, path, payload in requests:
            with self.subTest(method=method, path=path):
                self.terminals.calls.clear()
                status, _, response, _ = self.assert_json_response(
                    method, path, payload, token="é"
                )
                self.assertEqual(status, 403)
                self.assertEqual(response, {"error": "forbidden"})
                self.assertEqual(self.terminals.calls, [])

    def test_terminal_post_requires_a_json_object_before_manager_call(self):
        paths = [
            "/api/terminal/open",
            "/api/terminal/input",
            "/api/terminal/interrupt",
            "/api/terminal/close",
            "/api/terminal/unknown",
        ]
        invalid_payloads = [
            b'{"kind":',
            b"\xff",
            b"",
            b"[]",
            b'"scalar"',
            b"null",
            b"1",
            b"true",
        ]
        for path in paths:
            for payload in invalid_payloads:
                with self.subTest(path=path, payload=payload):
                    self.terminals.calls.clear()
                    status, _, response, _ = self.assert_json_response(
                        "POST", path, payload
                    )
                    self.assertEqual(status, 400)
                    self.assertEqual(response, {"error": "invalid json"})
                    self.assertEqual(self.terminals.calls, [])

    def test_open_route_passes_only_kind_and_label(self):
        status, headers, response, body = self.assert_json_response(
            "POST",
            "/api/terminal/open",
            {
                "kind": "ssh",
                "label": "生产A",
                "jump_pass": "server-secret",
                "id": "ignored",
                "text": "ignored",
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(
            response,
            {"id": "t1", "kind": "ssh", "label": "生产A", "status": "connecting"},
        )
        self.assertEqual(self.terminals.calls, [("open", "ssh", "生产A")])
        self.assertNotIn(b"server-secret", body)
        self.assertNotIn(b"jump_pass", body)

    def test_open_route_filters_sensitive_fields_from_manager_response(self):
        self.terminals.open_extra = {
            "jump_pass": "manager-secret",
            "secret": "manager-secret",
        }

        status, _, response, body = self.assert_json_response(
            "POST", "/api/terminal/open", {"kind": "ssh", "label": "生产A"}
        )

        self.assertEqual(status, 200)
        self.assertEqual(
            response,
            {"id": "t1", "kind": "ssh", "label": "生产A", "status": "connecting"},
        )
        self.assertNotIn(b"manager-secret", body)
        self.assertNotIn(b"jump_pass", body)
        self.assertNotIn(b"secret", body)

    def test_open_route_defaults_to_local(self):
        status, _, response, _ = self.assert_json_response(
            "POST", "/api/terminal/open", {}
        )

        self.assertEqual(status, 200)
        self.assertEqual(response["kind"], "local")
        self.assertEqual(self.terminals.calls, [("open", "local", None)])

    def test_input_route_writes_only_supplied_id_and_text(self):
        status, _, response, _ = self.assert_json_response(
            "POST",
            "/api/terminal/input",
            {"id": "t1", "text": "echo ok", "kind": "ssh", "label": "ignored"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(response, {"ok": True})
        self.assertEqual(self.terminals.calls, [("write", "t1", "echo ok")])

    def test_events_route_passes_id_and_nonnegative_since(self):
        status, _, response, _ = self.assert_json_response(
            "GET", "/api/terminal/events?id=t1&since=0&kind=ignored"
        )

        self.assertEqual(status, 200)
        self.assertEqual(response, self.terminals.event_response)
        self.assertEqual(self.terminals.calls, [("events", "t1", 0)])

    def test_events_route_defaults_since_to_zero(self):
        status, _, _, _ = self.assert_json_response(
            "GET", "/api/terminal/events?id=t1"
        )

        self.assertEqual(status, 200)
        self.assertEqual(self.terminals.calls, [("events", "t1", 0)])

    def test_events_route_rejects_invalid_since_before_manager_call(self):
        for since in ("-1", "invalid", "1.5", ""):
            with self.subTest(since=since):
                self.terminals.calls.clear()
                status, _, response, _ = self.assert_json_response(
                    "GET", f"/api/terminal/events?id=t1&since={since}"
                )
                self.assertEqual(status, 400)
                self.assertEqual(response, {"error": "invalid since"})
                self.assertEqual(self.terminals.calls, [])

    def test_interrupt_route_interrupts_supplied_session(self):
        status, _, response, _ = self.assert_json_response(
            "POST", "/api/terminal/interrupt", {"id": "t1", "text": "ignored"}
        )

        self.assertEqual(status, 200)
        self.assertEqual(response, {"ok": True})
        self.assertEqual(self.terminals.calls, [("interrupt", "t1")])

    def test_close_route_closes_supplied_session(self):
        status, _, response, _ = self.assert_json_response(
            "POST", "/api/terminal/close", {"id": "t1", "kind": "ignored"}
        )

        self.assertEqual(status, 200)
        self.assertEqual(response, {"ok": True})
        self.assertEqual(self.terminals.calls, [("close", "t1")])

    def test_static_index_replaces_port_and_terminal_token(self):
        status, _, body = self.request("GET", "/index.html")
        text = body.decode("utf-8")

        self.assertEqual(status, 200)
        self.assertIn(
            f'window.API_BASE = "http://127.0.0.1:{self.app.port}";', text
        )
        self.assertIn(f'window.TERMINAL_TOKEN = "{self.TOKEN}";', text)
        self.assertNotIn("__PORT__", text)
        self.assertNotIn("__TERMINAL_TOKEN__", text)
        self.assertNotIn("server-secret", text)

    def test_close_event_closes_all_terminals_before_server_shutdown(self):
        operations = []
        self.terminals.close_all = lambda: operations.append("close_all")
        server = SimpleNamespace(shutdown=lambda: operations.append("shutdown"))
        window = WebCurlGenerator.__new__(WebCurlGenerator)
        window.terminals = self.terminals
        window._server = server
        window._server_closed = False
        event = object()

        with mock.patch("curlgen.web_ui.QMainWindow.closeEvent") as parent_close:
            window.closeEvent(event)
            window.closeEvent(event)

        self.assertEqual(operations, ["close_all", "shutdown"])
        self.assertEqual(parent_close.call_count, 2)

    def test_terminal_options_method_is_not_added(self):
        status, _, _ = self.request("OPTIONS", "/api/terminal/open")

        self.assertEqual(status, 501)
        self.assertEqual(self.terminals.calls, [])

    def test_existing_api_does_not_require_terminal_token(self):
        status, headers, body = self.request("GET", "/api/history", token="")

        self.assertEqual(status, 200)
        self.assertEqual(dict(headers)["Access-Control-Allow-Origin"], "*")
        self.assertEqual(json.loads(body.decode("utf-8")), [])

    def test_non_terminal_post_keeps_lenient_malformed_json_handling(self):
        self.app.history.append({"url": "existing"})

        status, _, response, _ = self.assert_json_response(
            "POST", "/api/clear_history", b'{"ignored":', token=""
        )

        self.assertEqual(status, 200)
        self.assertEqual(response, {"ok": True})
        self.assertEqual(self.app.history, [])


if __name__ == "__main__":
    unittest.main()
