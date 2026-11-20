import hashlib
import io
import os
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from curlgen_mcp import paths
from curlgen_mcp.ssh import SSHError, StandardSSH


class FakeSocket:
    def __init__(self, label):
        self.label = label
        self.closed = False

    def settimeout(self, value):
        pass

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, server):
        self.server = server
        self.command = ""
        self.closed = False

    def settimeout(self, value):
        pass

    def exec_command(self, command):
        self.command = command
        self.server.handle_command(command)

    def recv_exit_status(self):
        return self.server.exit_code

    def makefile(self, mode):
        return io.BytesIO(self.server.stdout.encode())

    def makefile_stderr(self, mode):
        return io.BytesIO(self.server.stderr.encode())

    def close(self):
        self.closed = True


class FakeSFTP:
    def __init__(self, server):
        self.server = server
        self.closed = False

    def put(self, local, remote):
        self.server.files[remote] = Path(local).read_bytes()

    def get(self, remote, local):
        Path(local).write_bytes(self.server.files[remote])

    def stat(self, remote):
        class Stat:
            st_size = len(self.server.files[remote])

        return Stat()

    def close(self):
        self.closed = True


class FakeSSHServer:
    def __init__(self):
        self.files = {}
        self.stdout = "fake-host\n"
        self.stderr = ""
        self.exit_code = 0
        self.sessions = []

    def session(self):
        session = FakeSession(self)
        self.sessions.append(session)
        return session

    def handle_command(self, command):
        path = self._hash_path(command)
        if path is not None:
            if path not in self.files:
                self.exit_code = 1
                self.stdout = f"sha256sum: {path}: No such file"
            else:
                self.exit_code = 0
                self.stdout = f"{hashlib.sha256(self.files[path]).hexdigest()}  {path}"
        elif command.startswith("hostname"):
            self.stdout = "fake-host\n"

    def _hash_path(self, command):
        first = command.split("||", 1)[0].strip()
        try:
            parts = shlex.split(first)
        except ValueError:
            return None
        if parts and parts[0] in ("sha256sum", "shasum"):
            return parts[1] if len(parts) > 1 else None
        return None


class FakeTransport:
    def __init__(self, sock, server, label, fail_auth=None):
        self.sock = sock
        self.server = server
        self.label = label
        self.fail_auth = fail_auth
        self.auth_calls = []
        self.closed = False
        self.channel_args = None

    def start_client(self, timeout=None):
        pass

    def auth_password(self, username, password):
        self.auth_calls.append(("password", username, password))
        if self.fail_auth:
            raise self.fail_auth

    def open_channel(self, kind, dest, src, timeout=None):
        self.channel_args = (kind, dest, src)
        return FakeSocket("target")

    def open_session(self, timeout=None):
        return self.server.session()

    def close(self):
        self.closed = True


class FakeSFTPFactory:
    def __init__(self, server):
        self.server = server
        self.client = None

    def __call__(self, transport):
        self.client = FakeSFTP(self.server)
        return self.client


class StandardSSHTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            "CURLGEN_MCP_FILE_ROOTS": str(self.root),
            "CURLGEN_MCP_MAX_TRANSFER": "1024",
        })
        self.env.start()
        self.server = FakeSSHServer()
        self.transports = []
        self.sftp_factory = FakeSFTPFactory(self.server)
        self.cfg = {
            "jump_host": "10.0.0.1",
            "jump_port": "22",
            "jump_user": "jumper",
            "jump_pass": "jump-password",
            "target_host": "10.0.0.9",
            "target_port": "22",
            "target_user": "deploy",
            "target_pass": "target-password",
        }

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def make_ssh(self, cfg=None):
        def socket_factory(address, timeout=None):
            return FakeSocket("jump")

        def transport_factory(sock):
            label = "jump" if sock.label == "jump" else "target"
            transport = FakeTransport(sock, self.server, label)
            self.transports.append(transport)
            return transport

        return StandardSSH(
            cfg or self.cfg,
            socket_factory=socket_factory,
            transport_factory=transport_factory,
            sftp_factory=self.sftp_factory,
        )

    def test_connect_uses_direct_tcpip_and_password_auth(self):
        ssh = self.make_ssh()
        ssh.connect()
        self.assertEqual(len(self.transports), 2)
        jump, target = self.transports
        self.assertEqual(jump.auth_calls, [("password", "jumper", "jump-password")])
        self.assertEqual(
            jump.channel_args,
            ("direct-tcpip", ("10.0.0.9", 22), ("127.0.0.1", 0)),
        )
        self.assertEqual(target.auth_calls, [("password", "deploy", "target-password")])
        ssh.close()
        self.assertTrue(jump.closed)
        self.assertTrue(target.closed)

    def test_exec_returns_stdout_stderr_and_exit_code(self):
        self.server.stderr = "warning\n"
        self.server.exit_code = 3
        ssh = self.make_ssh()
        ssh.connect()
        result = ssh.exec("hostname", timeout=10)
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(result["stdout"], "fake-host\n")
        self.assertEqual(result["stderr"], "warning\n")
        ssh.close()

    def test_missing_jump_password_is_rejected(self):
        cfg = dict(self.cfg, jump_pass="")
        with self.assertRaises(SSHError) as ctx:
            self.make_ssh(cfg).connect()
        self.assertIn("密码", str(ctx.exception))

    def test_upload_and_download_round_trip_with_hash_verification(self):
        local = self.root / "upload.bin"
        local.write_bytes(b"hello sftp" * 4)
        remote = "/tmp/remote file.bin"
        ssh = self.make_ssh()
        ssh.connect()

        uploaded = ssh.upload(str(local), remote)
        self.assertEqual(self.server.files[remote], local.read_bytes())
        self.assertEqual(uploaded["size"], local.stat().st_size)
        self.assertEqual(uploaded["sha256"], hashlib.sha256(local.read_bytes()).hexdigest())

        target = self.root / "download.bin"
        downloaded = ssh.download(remote, str(target))
        self.assertEqual(target.read_bytes(), local.read_bytes())
        self.assertEqual(downloaded["sha256"], uploaded["sha256"])
        ssh.close()

    def test_upload_rejects_oversize(self):
        local = self.root / "big.bin"
        local.write_bytes(b"x" * 1025)
        ssh = self.make_ssh()
        ssh.connect()
        with self.assertRaises(ValueError):
            ssh.upload(str(local), "/tmp/big.bin")
        ssh.close()

    def test_download_rejects_remote_oversize(self):
        self.server.files["/tmp/big.bin"] = b"x" * 1025
        ssh = self.make_ssh()
        ssh.connect()
        with self.assertRaises(SSHError):
            ssh.download("/tmp/big.bin", str(self.root / "out.bin"))
        ssh.close()

    def test_transfer_without_connect_is_rejected(self):
        ssh = self.make_ssh()
        with self.assertRaises(SSHError):
            ssh.exec("hostname")

    def test_local_path_outside_roots_is_rejected(self):
        local = self.root / ".." / "outside.bin"
        local.write_bytes(b"x")
        ssh = self.make_ssh()
        ssh.connect()
        with self.assertRaises(ValueError):
            ssh.upload(str(local), "/tmp/x.bin")
        ssh.close()


if __name__ == "__main__":
    unittest.main()