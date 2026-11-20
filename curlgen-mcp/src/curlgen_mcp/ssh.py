"""Standard SSH jump-host execution and SFTP transfer.

Unlike GateShell, a normal OpenSSH jump host supports ``direct-tcpip`` and a
second SSH transport can be nested through it. File transfer uses SFTP and is
verified with size + SHA-256 (when the target shell provides ``sha256sum`` or
``shasum``).

Authentication order for the target: explicit password → explicit key →
ssh-agent → common ``~/.ssh/id_*`` keys → ``none``. The jump host uses the
password from ``ssh_config.json`` (or ``CURLGEN_JUMP_PASSWORD``).
"""

import logging
import os
import socket
from pathlib import Path

import paramiko

from . import paths
from .transfer import parse_sha256

logger = logging.getLogger("curlgen_mcp.ssh")


class SSHError(Exception):
    """Standard SSH connection or command failure."""


def _port(value, default=22):
    try:
        return int(str(value).strip()) if str(value).strip() else default
    except (TypeError, ValueError):
        return default


def _load_private_key(path):
    passphrase = os.environ.get("CURLGEN_KEY_PASSPHRASE") or None
    candidates = [
        getattr(paramiko, "Ed25519Key", None),
        getattr(paramiko, "RSAKey", None),
        getattr(paramiko, "ECDSAKey", None),
        getattr(paramiko, "DSSKey", None),
    ]
    for key_type in candidates:
        if key_type is None:
            continue
        try:
            return key_type.from_private_key_file(str(path), password=passphrase)
        except Exception:
            continue
    return None


class StandardSSH:
    """Password/key authenticated SSH through a standard jump host."""

    def __init__(
        self,
        config,
        connect_timeout=10,
        socket_factory=None,
        transport_factory=None,
        sftp_factory=None,
    ):
        self.config = dict(config or {})
        self.connect_timeout = float(connect_timeout)
        self._socket_factory = socket_factory or socket.create_connection
        self._transport_factory = transport_factory or paramiko.Transport
        self._sftp_factory = sftp_factory or paramiko.SFTPClient.from_transport
        self._jump_sock = None
        self._jump_transport = None
        self._target_sock = None
        self._target_transport = None

    # ── connection ──
    def _values(self):
        config = self.config
        jump_host = str(config.get("jump_host") or "").strip()
        jump_user = str(config.get("jump_user") or "").strip()
        jump_pass = str(config.get("jump_pass") or "")
        target_host = str(config.get("host") or config.get("target_host") or "").strip()
        target_user = str(
            config.get("user") or config.get("target_user") or jump_user
        ).strip()
        return {
            "jump_host": jump_host,
            "jump_port": _port(config.get("jump_port"), 22),
            "jump_user": jump_user,
            "jump_pass": jump_pass,
            "jump_key": str(config.get("jump_key") or "").strip(),
            "target_host": target_host,
            "target_port": _port(config.get("target_port") or config.get("port"), 22),
            "target_user": target_user,
            "target_pass": str(config.get("target_pass") or config.get("password") or ""),
            "target_key": str(config.get("target_key") or config.get("key") or "").strip(),
        }

    def connect(self):
        if self._target_transport is not None:
            return self
        values = self._values()
        if not (values["jump_host"] and values["jump_user"]):
            raise SSHError("跳板机配置不完整（主机/用户）")
        if not values["jump_pass"]:
            raise SSHError("跳板机配置不完整（缺少密码）")
        if not values["target_host"]:
            raise SSHError("目标机配置不完整（主机）")

        try:
            self._jump_sock = self._socket_factory(
                (values["jump_host"], values["jump_port"]),
                timeout=self.connect_timeout,
            )
            self._jump_transport = self._transport_factory(self._jump_sock)
            self._jump_transport.start_client(timeout=self.connect_timeout)
            self._authenticate(
                self._jump_transport,
                values["jump_user"],
                values["jump_pass"],
                values["jump_key"],
            )

            self._target_sock = self._jump_transport.open_channel(
                "direct-tcpip",
                (values["target_host"], values["target_port"]),
                ("127.0.0.1", 0),
            )
            self._target_transport = self._transport_factory(self._target_sock)
            self._target_transport.start_client(timeout=self.connect_timeout)
            self._authenticate(
                self._target_transport,
                values["target_user"],
                values["target_pass"],
                values["target_key"],
            )
        except SSHError:
            self.close()
            raise
        except paramiko.ssh_exception.AuthenticationException as exc:
            self.close()
            raise SSHError(f"SSH 认证失败: {exc}") from exc
        except Exception as exc:
            self.close()
            raise SSHError(f"SSH 连接失败: {exc}") from exc

        logger.info(
            "Standard SSH connected: %s@%s -> %s@%s",
            values["jump_user"],
            values["jump_host"],
            values["target_user"],
            values["target_host"],
        )
        return self

    def _authenticate(self, transport, username, password="", key_filename=""):
        if password:
            transport.auth_password(username, password)
            return

        if key_filename:
            key = _load_private_key(key_filename)
            if key is not None:
                transport.auth_publickey(username, key)
                return

        try:
            agent = paramiko.Agent()
            try:
                for key in agent.get_keys():
                    try:
                        transport.auth_publickey(username, key)
                        return
                    except paramiko.ssh_exception.AuthenticationException:
                        continue
            finally:
                try:
                    agent.close()
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("ssh-agent unavailable: %s", exc)

        for name in ("id_ed25519", "id_rsa", "id_ecdsa"):
            key_path = Path.home() / ".ssh" / name
            if not key_path.is_file():
                continue
            key = _load_private_key(key_path)
            if key is None:
                continue
            try:
                transport.auth_publickey(username, key)
                return
            except paramiko.ssh_exception.AuthenticationException:
                continue

        try:
            transport.auth_none(username)
        except Exception as exc:
            raise SSHError(
                f"目标机认证失败（{username}）：未提供密码/密钥，且 ssh-agent 和 ~/.ssh 密钥均不可用"
            ) from exc

    def _ensure(self):
        if self._target_transport is None:
            raise SSHError("SSH 尚未连接，请先调用 connect()")

    # ── command ──
    def exec(self, command, timeout=60):
        self._ensure()
        channel = None
        try:
            channel = self._target_transport.open_session(
                timeout=self.connect_timeout
            )
            channel.settimeout(float(timeout) + 5)
            channel.exec_command(command)
            stdout = channel.makefile("rb").read().decode("utf-8", "replace")
            stderr = channel.makefile_stderr("rb").read().decode("utf-8", "replace")
            exit_code = channel.recv_exit_status()
            return {
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "command": command,
            }
        except socket.timeout:
            raise SSHError(f"命令执行超时（>{timeout}s）") from None
        except SSHError:
            raise
        except Exception as exc:
            raise SSHError(f"远端命令执行失败: {exc}") from exc
        finally:
            if channel is not None:
                try:
                    channel.close()
                except Exception:
                    pass

    def _remote_sha256(self, remote_path):
        import shlex

        quoted = shlex.quote(remote_path)
        result = self.exec(
            f"sha256sum {quoted} 2>/dev/null || shasum -a 256 {quoted}",
            timeout=30,
        )
        digest = parse_sha256(result["stdout"])
        if result["exit_code"] != 0 or not digest:
            raise SSHError(
                f"无法校验远端文件 SHA-256: {result['stderr'].strip()[:160]}"
            )
        return digest

    # ── files ──
    def upload(self, local_path, remote_path, progress=None):
        resolved = paths.resolve_local_path(local_path, must_exist=True)
        data = paths.read_local_file(str(resolved))
        size = len(data)
        if not remote_path:
            raise SSHError("远端路径不能为空")
        self._ensure()
        sftp = None
        try:
            sftp = self._sftp_factory(self._target_transport)
            sftp.put(str(resolved), str(remote_path))
            remote_size = sftp.stat(str(remote_path)).st_size
            if remote_size != size:
                raise SSHError(
                    f"上传后大小不一致（本地 {size}，远端 {remote_size}）"
                )
            local_hash = paths.sha256_bytes(data)
            remote_hash = self._remote_sha256(str(remote_path))
            if local_hash != remote_hash:
                raise SSHError(
                    f"SHA-256 校验失败（本地 {local_hash}，远端 {remote_hash}）"
                )
            if progress:
                progress(size, size)
            return {
                "remote_path": str(remote_path),
                "size": size,
                "sha256": local_hash,
                "transport": "sftp",
            }
        except SSHError:
            raise
        except Exception as exc:
            raise SSHError(f"SFTP 上传失败: {exc}") from exc
        finally:
            if sftp is not None:
                try:
                    sftp.close()
                except Exception:
                    pass

    def download(self, remote_path, local_path, progress=None):
        if not remote_path:
            raise SSHError("远端路径不能为空")
        target = paths.resolve_local_path(local_path)
        self._ensure()
        sftp = None
        try:
            sftp = self._sftp_factory(self._target_transport)
            remote_size = sftp.stat(str(remote_path)).st_size
            limit = paths.max_transfer_bytes()
            if remote_size > limit:
                raise SSHError(
                    f"远端文件大小 {remote_size} 字节超过限制 {limit} 字节"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            sftp.get(str(remote_path), str(target))
            data = target.read_bytes()
            if len(data) != remote_size:
                raise SSHError(
                    f"下载后大小不一致（远端 {remote_size}，本地 {len(data)}）"
                )
            local_hash = paths.sha256_bytes(data)
            remote_hash = self._remote_sha256(str(remote_path))
            if local_hash != remote_hash:
                raise SSHError(
                    f"SHA-256 校验失败（远端 {remote_hash}，本地 {local_hash}）"
                )
            if progress:
                progress(len(data), len(data))
            return {
                "remote_path": str(remote_path),
                "local_path": str(target),
                "size": len(data),
                "sha256": local_hash,
                "transport": "sftp",
            }
        except SSHError:
            raise
        except Exception as exc:
            raise SSHError(f"SFTP 下载失败: {exc}") from exc
        finally:
            if sftp is not None:
                try:
                    sftp.close()
                except Exception:
                    pass

    def close(self):
        for obj in (
            self._target_transport,
            self._jump_transport,
            self._target_sock,
            self._jump_sock,
        ):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        self._target_transport = None
        self._jump_transport = None
        self._target_sock = None
        self._jump_sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc, tb):
        self.close()