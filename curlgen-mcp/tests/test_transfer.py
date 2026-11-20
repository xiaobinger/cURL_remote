import base64
import hashlib
import re
import shlex
import unittest

from curlgen_mcp.transfer import GateTransfer, TransferError


class FakeGateFS:
    """A tiny fake of the remote shell commands GateTransfer emits."""

    def __init__(self, files=None, max_bytes=8 * 1024 * 1024, hash_override=None):
        self.files = dict(files or {})
        self.max_bytes = max_bytes
        self.hash_override = hash_override
        self.commands = []

    def __call__(self, command, timeout=60):
        self.commands.append(command)
        if command.startswith(": > "):
            path = self._path(command[4:])
            self.files[path] = b""
            return 0, ""
        match = re.match(r"^printf %s '([A-Za-z0-9+/=]*)' \| base64 -d >> (.*)$", command)
        if match:
            payload, path_token = match.groups()
            path = self._path(path_token)
            self.files[path] = self.files.get(path, b"") + base64.b64decode(payload)
            return 0, ""
        match = re.match(r"^wc -c < (.*)$", command)
        if match:
            path = self._path(match.group(1))
            if path not in self.files:
                return 1, f"wc: {path}: No such file"
            return 0, str(len(self.files[path]))
        match = re.match(r"^base64 < (.*)$", command)
        if match:
            path = self._path(match.group(1))
            if path not in self.files:
                return 1, f"base64: {path}: No such file"
            encoded = base64.b64encode(self.files[path]).decode()
            return 0, "\n".join(encoded[i:i + 76] for i in range(0, len(encoded), 76))
        match = re.match(r"^sha256sum (.*?) 2>/dev/null \|\| shasum -a 256 (.*)$", command)
        if match:
            path = self._path(match.group(1))
            if path not in self.files:
                return 1, f"sha256sum: {path}: No such file"
            digest = self.hash_override or hashlib.sha256(self.files[path]).hexdigest()
            return 0, f"{digest}  {path}"
        return 127, f"unsupported fake command: {command[:80]}"

    def _path(self, token):
        parts = shlex.split(token)
        if not parts:
            raise AssertionError(f"bad path token: {token!r}")
        return parts[0]


class GateTransferTests(unittest.TestCase):
    def test_upload_writes_chunks_and_verifies_hash(self):
        fs = FakeGateFS()
        data = b"hello gate shell" * 10
        transfer = GateTransfer(fs, max_bytes=1024, chunk_size=7)
        result = transfer.upload(data, "/tmp/upload.bin")

        self.assertEqual(fs.files["/tmp/upload.bin"], data)
        self.assertEqual(result["size"], len(data))
        self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(result["chunks"], 23)
        self.assertTrue(any(cmd.startswith(": > ") for cmd in fs.commands))
        self.assertTrue(any(cmd.startswith("wc -c < ") for cmd in fs.commands))
        self.assertTrue(any(cmd.startswith("sha256sum ") for cmd in fs.commands))

    def test_upload_rejects_oversize_before_running_commands(self):
        fs = FakeGateFS()
        transfer = GateTransfer(fs, max_bytes=3)
        with self.assertRaises(TransferError):
            transfer.upload(b"1234", "/tmp/too-big.bin")
        self.assertEqual(fs.commands, [])

    def test_upload_hash_mismatch_raises(self):
        fs = FakeGateFS(hash_override="0" * 64)
        transfer = GateTransfer(fs, max_bytes=1024)
        with self.assertRaises(TransferError) as ctx:
            transfer.upload(b"data", "/tmp/x.bin")
        self.assertIn("校验", str(ctx.exception))

    def test_download_decodes_wrapped_base64(self):
        data = b"download me" * 30
        fs = FakeGateFS({"/tmp/remote.bin": data})
        transfer = GateTransfer(fs, max_bytes=1024)
        result = transfer.download("/tmp/remote.bin")
        self.assertEqual(result["data"], data)
        self.assertEqual(result["size"], len(data))
        self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())

    def test_download_empty_file(self):
        fs = FakeGateFS({"/tmp/empty.bin": b""})
        result = GateTransfer(fs, max_bytes=1024).download("/tmp/empty.bin")
        self.assertEqual(result["data"], b"")

    def test_download_rejects_remote_oversize_before_base64(self):
        fs = FakeGateFS({"/tmp/big.bin": b"123456"})
        transfer = GateTransfer(fs, max_bytes=5)
        with self.assertRaises(TransferError):
            transfer.download("/tmp/big.bin")
        self.assertFalse(any(cmd.startswith("base64 < ") for cmd in fs.commands))

    def test_download_hash_mismatch_raises(self):
        fs = FakeGateFS({"/tmp/x.bin": b"data"}, hash_override="f" * 64)
        with self.assertRaises(TransferError) as ctx:
            GateTransfer(fs, max_bytes=1024).download("/tmp/x.bin")
        self.assertIn("校验", str(ctx.exception))

    def test_paths_with_spaces_and_quotes_are_shell_quoted(self):
        fs = FakeGateFS()
        path = "/tmp/a b'c.txt"
        GateTransfer(fs, max_bytes=1024).upload(b"x", path)
        self.assertIn(path, fs.files)
        self.assertTrue(all("a b'\"'\"'c.txt" in cmd or "a b" in cmd for cmd in fs.commands))

    def test_remote_failure_raises_transfer_error(self):
        def failing(command, timeout=60):
            return 1, "remote exploded"

        with self.assertRaises(TransferError) as ctx:
            GateTransfer(failing).download("/tmp/missing.bin")
        self.assertIn("remote exploded", str(ctx.exception))

    def test_missing_sha256sum_output_is_an_error(self):
        def no_hash(command, timeout=60):
            if command.startswith("sha256sum"):
                return 0, ""
            if command.startswith("wc -c"):
                return 0, "3"
            return 0, ""

        with self.assertRaises(TransferError):
            GateTransfer(no_hash, max_bytes=10).download("/tmp/x.bin")


if __name__ == "__main__":
    unittest.main()