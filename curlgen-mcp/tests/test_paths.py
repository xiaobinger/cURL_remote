import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from curlgen_mcp import paths


class PathTests(unittest.TestCase):
    def test_file_roots_override_is_split_by_os_pathsep(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with patch.dict(os.environ, {"CURLGEN_MCP_FILE_ROOTS": os.pathsep.join([a, b])}):
                roots = [str(p) for p in paths.allowed_roots()]
            self.assertEqual(roots, [str(Path(a).resolve()), str(Path(b).resolve())])

    def test_relative_path_resolves_under_cwd(self):
        resolved = paths.resolve_local_path("some-file.txt")
        self.assertEqual(resolved, (Path.cwd() / "some-file.txt").resolve())

    def test_path_outside_allowed_roots_is_rejected(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "secret.txt"
            target.write_text("x", encoding="utf-8")
            with patch.dict(os.environ, {"CURLGEN_MCP_FILE_ROOTS": root}):
                with self.assertRaises(ValueError) as ctx:
                    paths.resolve_local_path(str(target), must_exist=True)
            self.assertIn("允许", str(ctx.exception))

    def test_path_inside_allowed_root_is_accepted(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "inside.txt"
            target.write_text("x", encoding="utf-8")
            with patch.dict(os.environ, {"CURLGEN_MCP_FILE_ROOTS": root}):
                resolved = paths.resolve_local_path(str(target), must_exist=True)
            self.assertEqual(resolved, target.resolve())

    def test_traversal_outside_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / ".." / "escape.txt"
            with patch.dict(os.environ, {"CURLGEN_MCP_FILE_ROOTS": root}):
                with self.assertRaises(ValueError):
                    paths.resolve_local_path(str(target))

    def test_missing_upload_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {"CURLGEN_MCP_FILE_ROOTS": root}):
                with self.assertRaises(ValueError) as ctx:
                    paths.resolve_local_path(str(Path(root) / "nope.txt"), must_exist=True)
            self.assertIn("不存在", str(ctx.exception))

    def test_max_transfer_env_override(self):
        with patch.dict(os.environ, {"CURLGEN_MCP_MAX_TRANSFER": "12345"}):
            self.assertEqual(paths.max_transfer_bytes(), 12345)

    def test_max_transfer_invalid_falls_back_to_default(self):
        with patch.dict(os.environ, {"CURLGEN_MCP_MAX_TRANSFER": "abc"}):
            self.assertEqual(paths.max_transfer_bytes(), paths.DEFAULT_MAX_TRANSFER)

    def test_max_transfer_is_clamped(self):
        with patch.dict(os.environ, {"CURLGEN_MCP_MAX_TRANSFER": "999999999999"}):
            self.assertEqual(paths.max_transfer_bytes(), paths.MAX_TRANSFER_LIMIT)
        with patch.dict(os.environ, {"CURLGEN_MCP_MAX_TRANSFER": "1"}):
            self.assertEqual(paths.max_transfer_bytes(), 1)

    def test_chunk_size_env_override_and_bounds(self):
        with patch.dict(os.environ, {"CURLGEN_MCP_CHUNK_SIZE": "2048"}):
            self.assertEqual(paths.transfer_chunk_size(), 2048)
        with patch.dict(os.environ, {"CURLGEN_MCP_CHUNK_SIZE": "nope"}):
            self.assertEqual(paths.transfer_chunk_size(), paths.DEFAULT_CHUNK_SIZE)
        with patch.dict(os.environ, {"CURLGEN_MCP_CHUNK_SIZE": "1"}):
            self.assertEqual(paths.transfer_chunk_size(), paths.MIN_CHUNK_SIZE)

    def test_read_local_limited(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "a.bin"
            target.write_bytes(b"0123456789")
            with patch.dict(os.environ, {"CURLGEN_MCP_FILE_ROOTS": root}):
                with self.assertRaises(ValueError):
                    paths.read_local_file(str(target), max_bytes=5)
                self.assertEqual(paths.read_local_file(str(target), max_bytes=10), b"0123456789")

    def test_sha256_bytes(self):
        self.assertEqual(
            paths.sha256_bytes(b"abc"),
            hashlib.sha256(b"abc").hexdigest(),
        )

    def test_max_response_env_override_and_bounds(self):
        with patch.dict(os.environ, {"CURLGEN_MCP_MAX_RESPONSE": "2048"}):
            self.assertEqual(paths.max_response_bytes(), 2048)
        with patch.dict(os.environ, {"CURLGEN_MCP_MAX_RESPONSE": "bad"}):
            self.assertEqual(paths.max_response_bytes(), paths.DEFAULT_MAX_RESPONSE)
        with patch.dict(os.environ, {"CURLGEN_MCP_MAX_RESPONSE": "999999999"}):
            self.assertEqual(paths.max_response_bytes(), paths.MAX_RESPONSE_LIMIT)


if __name__ == "__main__":
    unittest.main()