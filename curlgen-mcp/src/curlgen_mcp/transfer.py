"""File transfer over a GateShell interactive shell.

GateShell has no SFTP and no ``exec``; files are moved through base64 commands
executed in the target shell. Every transfer is bounded by
``CURLGEN_MCP_MAX_TRANSFER`` and verified by size + SHA-256 before success.
"""

import base64
import re
import shlex

from . import paths


class TransferError(Exception):
    """Remote transfer failed (message is safe to show to the user)."""


def parse_sha256(text):
    match = re.search(r"\b([0-9a-fA-F]{64})\b", text or "")
    return match.group(1).lower() if match else None


class GateTransfer:
    """Upload/download through a ``run(command, timeout) -> (code, output)`` callable."""

    def __init__(self, run, max_bytes=None, chunk_size=None, timeout=60):
        self._run_command = run
        self.max_bytes = (
            paths.max_transfer_bytes() if max_bytes is None else int(max_bytes)
        )
        self.chunk_size = (
            paths.transfer_chunk_size() if chunk_size is None else int(chunk_size)
        )
        self.timeout = timeout

    def _run(self, command):
        exit_code, output = self._run_command(command, timeout=self.timeout)
        if exit_code != 0:
            tail = " ".join((output or "").split())[-200:]
            raise TransferError(
                f"远端命令失败（exit={exit_code}）: {command.splitlines()[0][:120]}"
                + (f"，输出：{tail}" if tail else "")
            )
        return output or ""

    def _remote_size(self, remote_path):
        output = self._run(f"wc -c < {shlex.quote(remote_path)}")
        match = re.search(r"(\d+)", output)
        if not match:
            raise TransferError(f"无法读取远端文件大小: {' '.join(output.split())[:160]}")
        return int(match.group(1))

    def _remote_sha256(self, remote_path):
        quoted = shlex.quote(remote_path)
        output = self._run(
            f"sha256sum {quoted} 2>/dev/null || shasum -a 256 {quoted}"
        )
        digest = parse_sha256(output)
        if not digest:
            raise TransferError(f"无法校验远端文件 SHA-256: {' '.join(output.split())[:160]}")
        return digest

    def upload(self, data, remote_path, progress=None):
        data = bytes(data)
        size = len(data)
        if size > self.max_bytes:
            raise TransferError(
                f"文件大小 {size} 字节超过限制 {self.max_bytes} 字节"
            )
        if not remote_path:
            raise TransferError("远端路径不能为空")

        quoted = shlex.quote(remote_path)
        self._run(f": > {quoted}")
        chunk_count = 0
        for offset in range(0, size, self.chunk_size):
            chunk = data[offset:offset + self.chunk_size]
            encoded = base64.b64encode(chunk).decode("ascii")
            self._run(f"printf %s '{encoded}' | base64 -d >> {quoted}")
            chunk_count += 1
            if progress:
                progress(min(offset + len(chunk), size), size)

        remote_size = self._remote_size(remote_path)
        if remote_size != size:
            raise TransferError(
                f"上传后大小不一致（本地 {size}，远端 {remote_size}）"
            )
        local_hash = paths.sha256_bytes(data)
        remote_hash = self._remote_sha256(remote_path)
        if local_hash != remote_hash:
            raise TransferError(
                f"SHA-256 校验失败（本地 {local_hash}，远端 {remote_hash}）"
            )
        return {
            "remote_path": remote_path,
            "size": size,
            "sha256": local_hash,
            "chunks": chunk_count,
            "transport": "gateshell-base64",
        }

    def download(self, remote_path, progress=None):
        if not remote_path:
            raise TransferError("远端路径不能为空")
        size = self._remote_size(remote_path)
        if size > self.max_bytes:
            raise TransferError(
                f"远端文件大小 {size} 字节超过限制 {self.max_bytes} 字节"
            )
        output = self._run(f"base64 < {shlex.quote(remote_path)}")
        encoded = "".join(output.split())
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception:
            raise TransferError("远端 base64 输出无法解析（文件可能不可读）") from None
        if len(data) != size:
            raise TransferError(
                f"下载后大小不一致（远端 {size}，解码 {len(data)}）"
            )
        local_hash = paths.sha256_bytes(data)
        remote_hash = self._remote_sha256(remote_path)
        if local_hash != remote_hash:
            raise TransferError(
                f"SHA-256 校验失败（远端 {remote_hash}，本地 {local_hash}）"
            )
        if progress:
            progress(len(data), len(data))
        return {
            "remote_path": remote_path,
            "data": data,
            "size": len(data),
            "sha256": local_hash,
            "transport": "gateshell-base64",
        }