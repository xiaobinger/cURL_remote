import asyncio
import inspect
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from curlgen_mcp import server  # noqa: E402

EXPECTED_TOOLS = {
    "list_environments",
    "http_request",
    "generate_code",
    "ssh_list_targets",
    "ssh_exec",
    "ssh_upload",
    "ssh_download",
    "remote_http_request",
    "bastion_login",
    "bastion_list_hosts",
    "bastion_connect",
    "bastion_exec",
    "bastion_status",
    "bastion_logout",
    "bastion_upload",
    "bastion_download",
}


class _Handler(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).seen.append(
            {"path": self.path, **{k.lower(): v for k, v in self.headers.items()}}
        )
        payload = json.dumps({"ok": True, "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = mock.patch.dict(
            os.environ,
            {
                "CURLGEN_CONFIG_DIR": str(self.root),
                "CURLGEN_MCP_FILE_ROOTS": str(self.root),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        server._BASTION.update(
            {"session": None, "target": "", "label": "", "jump": {}}
        )

    def tearDown(self):
        session = server._BASTION.get("session")
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        server._BASTION.update(
            {"session": None, "target": "", "label": "", "jump": {}}
        )

    def _write(self, name, data):
        (self.root / name).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )


class SpecTests(_ServerCase):
    def test_build_spec_maps_headers_auth_and_sign(self):
        spec = server.build_spec(
            url="https://api.example.com/q",
            method="post",
            headers={"X-Trace": "t-1", "X-Count": 2},
            params=[["page", 1], ["q", "a b"]],
            body={"amount": 10},
            body_type="json",
            auth_type="bearer",
            auth_token="tok",
            sign_placement="header",
            sign_field="signature",
            sign_header="X-Signature",
            sign_algo="sha256",
            sign_key="secret",
            timeout=12,
        )
        self.assertEqual(spec["method"], "POST")
        self.assertEqual(spec["headers"], [["X-Trace", "t-1"], ["X-Count", "2"]])
        self.assertEqual(spec["params"], [["page", "1"], ["q", "a b"]])
        self.assertEqual(spec["body"], {"amount": 10})
        self.assertEqual(spec["auth"]["type"], "bearer")
        self.assertEqual(spec["auth"]["token"], "tok")
        self.assertEqual(spec["sign"]["placement"], "header")
        self.assertEqual(spec["sign"]["field"], "signature")
        self.assertEqual(spec["sign"]["header"], "X-Signature")
        self.assertEqual(spec["sign"]["algo"], "sha256")
        self.assertEqual(spec["sign"]["secret"], "secret")
        self.assertEqual(spec["timeout"], 12)

    def test_build_spec_converts_form_and_raw_bodies(self):
        form = server.build_spec(url="http://x", body={"a": 1, "b": "2"}, body_type="form")
        self.assertEqual(form["body"], "a=1\nb=2")
        raw = server.build_spec(url="http://x", body=[["a", 1], ["b", 2]], body_type="raw")
        self.assertEqual(raw["body"], "a=1&b=2")

    def test_build_spec_rejects_unknown_body_type(self):
        with self.assertRaises(ValueError):
            server.build_spec(url="http://x", body_type="xml")

    def test_public_request_removes_secret(self):
        req = {
            "method": "POST",
            "url": "http://x",
            "headers": {},
            "body": None,
            "body_type": "none",
            "params": [],
            "sign": {"enabled": True, "value": "ABC", "secret": "TOP"},
        }
        public = server._public_request(req)
        self.assertNotIn("TOP", json.dumps(public, ensure_ascii=False))
        self.assertEqual(public["sign"]["value"], "ABC")


class EnvironmentAndCodegenTests(_ServerCase):
    def _seed(self):
        self._write(
            "env_configs.json",
            [
                {
                    "alias": "生产",
                    "enable_sign": True,
                    "key": "ENV_SECRET",
                    "algo": "MD5",
                    "param_name": "sign",
                    "custom_params": [["host", "api.example.com"]],
                }
            ],
        )
        self._write(
            "ssh_config.json",
            {
                "jump_host": "jump.example.com",
                "jump_port": "22",
                "jump_user": "u",
                "jump_pass": "JUMP_SECRET",
                "targets": [
                    {
                        "label": "A",
                        "host": "1.1.1.1",
                        "port": "22",
                        "user": "root",
                        "password": "TARGET_SECRET",
                    }
                ],
            },
        )

    def test_list_environments_and_targets_never_leak_secrets(self):
        self._seed()
        env_result = server.list_environments()
        ssh_result = server.ssh_list_targets()
        rendered = json.dumps([env_result, ssh_result], ensure_ascii=False)
        for secret in ("ENV_SECRET", "JUMP_SECRET", "TARGET_SECRET"):
            self.assertNotIn(secret, rendered)
        self.assertEqual(env_result["environments"][0]["alias"], "生产")
        self.assertEqual(env_result["ssh"]["targets"][0]["target_password_set"], True)
        self.assertTrue(ssh_result["targets"][0]["gateshell_ready"])

    def test_generate_code_uses_environment_and_masks_secret(self):
        self._seed()
        result = server.generate_code(
            url="https://{{host}}/open/api",
            method="POST",
            body={"amount": 10},
            environment="生产",
            lang="python",
        )
        self.assertTrue(result["ok"])
        self.assertIn("requests.request", result["code"])
        compile(result["code"], "<generated>", "exec")
        self.assertNotIn("ENV_SECRET", json.dumps(result, ensure_ascii=False))
        self.assertEqual(result["request"]["sign"]["enabled"], True)
        self.assertTrue(result["request"]["sign"]["value"])
        self.assertEqual(result["request"]["url"], "https://api.example.com/open/api")

        runtime = server.generate_code(
            url="https://{{host}}/runtime",
            method="POST",
            body={"amount": 10},
            environment="生产",
            lang="javascript",
            sign_mode="runtime",
        )
        self.assertTrue(runtime["ok"])
        self.assertIn("CURLGEN_SIGN_KEY", runtime["code"])
        self.assertNotIn("ENV_SECRET", json.dumps(runtime, ensure_ascii=False))

    def test_generate_code_rejects_bad_language(self):
        result = server.generate_code(url="http://x", lang="brainfuck")
        self.assertFalse(result["ok"])
        self.assertIn("brainfuck", result["error"])


class ToolRegistrationTests(unittest.TestCase):
    def test_exactly_the_documented_tools_are_registered(self):
        tools = server.mcp.list_tools()
        if inspect.isawaitable(tools):
            tools = asyncio.run(tools)
        names = {tool.name for tool in tools}
        self.assertEqual(names, EXPECTED_TOOLS)


class LocalHttpTests(_ServerCase):
    def setUp(self):
        super().setUp()
        _Handler.seen = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def test_http_request_executes_and_returns_curl(self):
        result = server.http_request(
            url=self.base + "/hello",
            params={"q": "a b"},
            timeout=5,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["response"]["status"], 200)
        self.assertIn("/hello?q=a%20b", result["response"]["url"])
        self.assertIn("curl", result["curl"])
        self.assertEqual(_Handler.seen[0]["path"], "/hello?q=a%20b")

    def test_http_request_places_signature_in_header(self):
        result = server.http_request(
            url=self.base + "/signed",
            params={"b": "2", "a": "1"},
            sign_enabled=True,
            sign_placement="header",
            sign_field="sign",
            sign_header="X-Signature",
            sign_algo="MD5",
            sign_key="secret",
            timeout=5,
        )
        self.assertTrue(result["ok"], result)
        self.assertIn("x-signature", _Handler.seen[0])
        self.assertEqual(result["request"]["sign"]["placement"], "header")
        self.assertNotIn("secret", json.dumps(result["request"], ensure_ascii=False))

    def test_http_request_reports_connection_errors(self):
        result = server.http_request(url="http://127.0.0.1:1/", timeout=2)
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"])


class RemoteHttpTests(_ServerCase):
    class _FakeSession:
        def __init__(self):
            self.connected = []
            self.commands = []
            self.closed = False

        def connect_host(self, host):
            self.connected.append(host)

        def run(self, command, timeout=60):
            self.commands.append((command, timeout))
            return 0, "__CG_HTTP_STATS__200 OK|0.01|12\n__CG_HTTP_BODY__\naGVsbG8=\n__CG_HTTP_END__\n"

        def close(self):
            self.closed = True

    def test_gateshell_request_runs_generated_curl_and_closes(self):
        fake = self._FakeSession()
        with mock.patch.object(server, "_resolve_target", return_value={"label": "A", "host": "1.1.1.1"}), \
                mock.patch.object(server, "open_session", return_value=fake), \
                mock.patch.object(server, "parse_remote_output", return_value={"status": 200, "body": "hello"}):
            result = server.remote_http_request(
                url="http://example.com",
                transport="gateshell",
                timeout=5,
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(fake.connected, ["1.1.1.1"])
        self.assertIn("curl", fake.commands[0][0])
        self.assertTrue(fake.closed)
        self.assertEqual(result["response"]["transport"], "gateshell-curl")

    def test_ssh_transport_executes_remote_curl_and_closes(self):
        class FakeSSH:
            def __init__(self, cfg):
                self.closed = False
                self.commands = []

            def connect(self):
                return self

            def exec(self, command, timeout=60):
                self.commands.append((command, timeout))
                return {
                    "exit_code": 0,
                    "stdout": "__CG_HTTP_STATS__200 OK|0.01|2\n",
                    "stderr": "",
                }

            def close(self):
                self.closed = True

        created = []

        def factory(cfg):
            client = FakeSSH(cfg)
            created.append(client)
            return client

        with mock.patch.object(server, "_resolve_target", return_value={"label": "A", "host": "1.1.1.1"}), \
                mock.patch.object(server, "StandardSSH", side_effect=factory), \
                mock.patch.object(server, "parse_remote_output", return_value={"status": 200, "body": "ok"}):
            result = server.remote_http_request(
                url="http://example.com",
                transport="ssh",
                timeout=5,
            )
        self.assertTrue(result["ok"], result)
        self.assertIn("curl", created[0].commands[0][0])
        self.assertTrue(created[0].closed)
        self.assertEqual(result["response"]["transport"], "ssh-curl")

    def test_remote_size_limit_error_is_reported(self):
        fake = self._FakeSession()
        fake.run = lambda command, timeout=60: (63, "")
        with mock.patch.object(server, "_resolve_target", return_value={"label": "A", "host": "1.1.1.1"}), \
                mock.patch.object(server, "open_session", return_value=fake):
            result = server.remote_http_request(
                url="http://example.com",
                transport="gateshell",
                timeout=5,
            )
        self.assertFalse(result["ok"])
        self.assertIn("大小限制", result["error"])
        self.assertTrue(fake.closed)


class StandardSshTests(_ServerCase):
    class _FakeClient:
        def __init__(self, cfg):
            self.cfg = cfg
            self.closed = False

        def connect(self):
            return self

        def exec(self, command, timeout=60):
            return {"exit_code": 0, "stdout": "ok", "stderr": "", "command": command}

        def upload(self, local_path, remote_path):
            return {"remote_path": "a", "size": 1}

        def download(self, remote_path, local_path):
            return {"local_path": "a", "size": 1}

        def close(self):
            self.closed = True

    def test_ssh_exec_closes_client(self):
        created = []

        def factory(cfg):
            client = self._FakeClient(cfg)
            created.append(client)
            return client

        with mock.patch.object(server, "_resolve_target", return_value={"label": "A", "host": "1.1.1.1"}), \
                mock.patch.object(server, "StandardSSH", side_effect=factory):
            result = server.ssh_exec("uptime")
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["stdout"], "ok")
        self.assertTrue(created[0].closed)

    def test_ssh_upload_and_download_delegate(self):
        with mock.patch.object(server, "_resolve_target", return_value={"label": "A", "host": "1.1.1.1"}), \
                mock.patch.object(server, "StandardSSH", side_effect=lambda cfg: self._FakeClient(cfg)):
            uploaded = server.ssh_upload("local.txt", "/tmp/a")
            downloaded = server.ssh_download("/tmp/a", "local.txt")
        self.assertTrue(uploaded["ok"])
        self.assertTrue(downloaded["ok"])


class BastionTests(_ServerCase):
    class _FakeGate:
        def __init__(self):
            self.closed = False
            self.connected = []
            self.commands = []
            self.uploads = []

        def total_hosts(self):
            return 2

        def list_hosts(self):
            return [
                {"ip": "10.0.0.1", "name": "web-1"},
                {"ip": "10.0.0.2", "name": "db-1"},
            ]

        def find_host(self, host):
            return next(
                (item for item in self.list_hosts() if host in (item["ip"], item["name"])),
                None,
            )

        def connect_host(self, host):
            self.connected.append(host)

        def run(self, command, timeout=60):
            self.commands.append((command, timeout))
            return 0, "out"

        def upload(self, data, remote_path):
            self.uploads.append((data, remote_path))
            return {"remote_path": remote_path, "size": len(data)}

        def download(self, remote_path):
            return {"remote_path": remote_path, "size": 3, "data": b"abc"}

        def close(self):
            self.closed = True

    def test_full_bastion_workflow(self):
        fake = self._FakeGate()
        with mock.patch.object(server, "open_session", return_value=fake):
            login = server.bastion_login()
            listed = server.bastion_list_hosts()
            connected = server.bastion_connect("web-1")
            executed = server.bastion_exec("uptime")
            status = server.bastion_status()
            logout = server.bastion_logout()

        self.assertTrue(login["ok"])
        self.assertEqual(login["total_hosts"], 2)
        self.assertTrue(listed["ok"])
        self.assertEqual(len(listed["hosts"]), 2)
        self.assertTrue(connected["ok"])
        self.assertEqual(connected["target"], "10.0.0.1")
        self.assertTrue(executed["ok"])
        self.assertEqual(executed["exit_code"], 0)
        self.assertEqual(fake.connected, ["10.0.0.1"])
        self.assertTrue(status["logged_in"])
        self.assertTrue(logout["ok"])
        self.assertTrue(fake.closed)
        self.assertFalse(server.bastion_status()["logged_in"])

    def test_bastion_requires_login_and_target(self):
        executed = server.bastion_exec("uptime")
        self.assertFalse(executed["ok"])
        self.assertIn("bastion_login", executed["error"])

        fake = self._FakeGate()
        with mock.patch.object(server, "open_session", return_value=fake):
            server.bastion_login()
        executed = server.bastion_exec("uptime")
        self.assertFalse(executed["ok"])
        self.assertIn("bastion_connect", executed["error"])

    def test_switching_target_closes_the_previous_shell(self):
        first, second = self._FakeGate(), self._FakeGate()
        with mock.patch.object(server, "open_session", side_effect=[first, second]):
            server.bastion_login()
            server.bastion_connect("web-1")
            switched = server.bastion_connect("db-1")

        self.assertTrue(switched["ok"], switched)
        self.assertEqual(switched["target"], "10.0.0.2")
        self.assertTrue(first.closed)
        self.assertEqual(second.connected, ["10.0.0.2"])
        self.assertEqual(server.bastion_status()["target"], "10.0.0.2")

    def test_bastion_upload_and_download(self):
        local = self.root / "payload.bin"
        local.write_bytes(b"hello")
        target = self.root / "saved.bin"
        fake = self._FakeGate()
        with mock.patch.object(server, "open_session", return_value=fake):
            server.bastion_login()
            server.bastion_connect("10.0.0.2")
            uploaded = server.bastion_upload(str(local), "/tmp/payload.bin")
            downloaded = server.bastion_download("/tmp/x", str(target))

        self.assertTrue(uploaded["ok"])
        self.assertEqual(fake.uploads[0][0], b"hello")
        self.assertTrue(downloaded["ok"])
        self.assertEqual(target.read_bytes(), b"abc")
        self.assertNotIn("data", downloaded["transfer"])


if __name__ == "__main__":
    unittest.main()