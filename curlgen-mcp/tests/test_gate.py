import logging
import socket
import threading
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import paramiko
from curlgen_mcp import gate
from curlgen_mcp.gate import (
    GateError,
    GateShellSession,
    _extract_body,
    parse_gate_hosts,
    strip_ansi,
    wrap_command,
)


MENU = (
    "Quit: Use q or Ctrl+C to quit\r\n"
    "001: web-01(10.0.0.1)   10.0.0.1:22   ssh   root\r\n"
    "002: db 01(10.0.0.2)    10.0.0.2:2222 ssh   admin\r\n"
    "003: tomcat01          10.0.0.3:22   ssh   ops\r\n"
    "3/90, name-asc\r\n"
)


class GateParsingTests(unittest.TestCase):
    def test_strip_ansi_removes_csi_osc_and_normalizes_crlf(self):
        raw = "\x1b[2J\x1b]0;title\x07hello\r\n\x1b[31mred\x1b[0m\r"
        self.assertEqual(strip_ansi(raw), "hello\nred\n")

    def test_parse_gate_hosts_reads_all_columns(self):
        hosts = parse_gate_hosts(MENU)
        self.assertEqual(len(hosts), 3)
        self.assertEqual(hosts[0], {
            "index": "001",
            "name": "web-01(10.0.0.1)",
            "ip": "10.0.0.1",
            "port": 22,
            "proto": "ssh",
            "user": "root",
        })
        self.assertEqual(hosts[1]["port"], 2222)
        self.assertEqual(hosts[2]["name"], "tomcat01")

    def test_parse_gate_hosts_deduplicates_repainted_rows(self):
        text = MENU + "001: web-01(10.0.0.1) 10.0.0.1:22 ssh root\r\n"
        self.assertEqual(len(parse_gate_hosts(text)), 3)

    def test_parse_gate_hosts_ignores_noise(self):
        self.assertEqual(parse_gate_hosts("Quit: Use q\nno hosts here"), [])

    def test_wrap_command_contains_start_end_and_exit_code(self):
        wrapped, start, end = wrap_command("hostname", token="abc123")
        self.assertIn(f"echo {start};", wrapped)
        self.assertIn("hostname 2>&1", wrapped)
        self.assertIn(f"echo {end}$__ec", wrapped)
        self.assertIn("__CG_S_abc123__", wrapped)
        self.assertIn("__CG_E_abc123__", wrapped)

    def test_extract_body_ignores_echoed_command_and_prompt(self):
        start, end = "__CG_S_t__", "__CG_E_t__"
        raw = (
            f"echo {start}; hostname 2>&1; __ec=$?; echo; echo {end}$__ec\r\n"
            f"{start}\r\nmy-host\r\n{end}0\r\n[root@my-host ~]# "
        ).encode()
        body = _extract_body(raw, start, end)
        self.assertEqual(body.strip(), "my-host")

    def test_extract_body_returns_empty_without_markers(self):
        self.assertEqual(_extract_body(b"just output", "S", "E"), "just output")


class LegacyHostKeyTests(unittest.TestCase):
    """GateShell appliances often only offer the SHA-1 ``ssh-rsa`` host key."""

    @classmethod
    def setUpClass(cls):
        probe_sock, peer = socket.socketpair()
        transport = paramiko.Transport(probe_sock)
        cls.ssh_rsa_supported = "ssh-rsa" in transport.preferred_keys
        transport.close()
        probe_sock.close()
        peer.close()

    def test_ssh_rsa_only_bastion_completes_the_handshake(self):
        if not self.ssh_rsa_supported:
            self.skipTest("installed paramiko dropped ssh-rsa; covered by the pin test")

        paramiko_log = logging.getLogger("paramiko.transport")
        previous_level = paramiko_log.level
        paramiko_log.setLevel(logging.CRITICAL)
        self.addCleanup(paramiko_log.setLevel, previous_level)

        class _AcceptAll(paramiko.ServerInterface):
            def check_auth_password(self, username, password):
                return paramiko.AUTH_SUCCESSFUL

            def get_allowed_auths(self, username):
                return "password"

        host_key = paramiko.RSAKey.generate(2048)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        address = listener.getsockname()

        def serve():
            conn, _ = listener.accept()
            server = paramiko.Transport(conn)
            server.add_server_key(host_key)
            server._preferred_keys = ("ssh-rsa",)
            try:
                server.start_server(server=_AcceptAll())
            except (OSError, EOFError, paramiko.SSHException):
                pass  # the client hangs up right after the handshake
            finally:
                server.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()

        session = GateShellSession(address[0], address[1], "user", "secret")
        sock = socket.create_connection(address, timeout=5)
        transport = session._start_transport(paramiko, sock)
        try:
            self.assertEqual(
                transport.get_remote_server_key().get_name(), "ssh-rsa"
            )
        finally:
            transport.close()
            sock.close()
            session.close()

    def test_unacceptable_host_key_error_tells_how_to_fix(self):
        class _FakeTransport:
            def __init__(self, sock):
                pass

            def start_client(self, timeout=None):
                raise paramiko.ssh_exception.IncompatiblePeer(
                    "Incompatible ssh peer (no acceptable host key)"
                )

            def close(self):
                pass

        session = GateShellSession("bastion", 22, "user", "secret")
        with mock.patch.object(socket, "create_connection", return_value=socket.socket()), \
                mock.patch.object(paramiko, "Transport", _FakeTransport):
            with self.assertRaises(GateError) as raised:
                session.open()
        message = str(raised.exception)
        self.assertIn("ssh-rsa", message)
        self.assertIn("paramiko<5", message)

    def test_dependency_pins_paramiko_below_five(self):
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        paramiko_spec = next(
            item for item in data["project"]["dependencies"] if item.startswith("paramiko")
        )
        self.assertIn("<5", paramiko_spec)


if __name__ == "__main__":
    unittest.main()