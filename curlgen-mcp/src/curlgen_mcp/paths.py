"""Local file access policy and size limits for transfer tools.

Only files below the allowed roots can be read from or written to. Roots come
from ``CURLGEN_MCP_FILE_ROOTS`` (``os.pathsep`` separated); the default is the
user's home directory plus the current working directory. Symlinks are resolved
before the check so a link cannot escape a root.
"""

import hashlib
import os
from pathlib import Path

DEFAULT_MAX_TRANSFER = 8 * 1024 * 1024
MAX_TRANSFER_LIMIT = 100 * 1024 * 1024
DEFAULT_CHUNK_SIZE = 64 * 1024
MIN_CHUNK_SIZE = 1024
MAX_CHUNK_SIZE = 1024 * 1024
DEFAULT_MAX_RESPONSE = 2 * 1024 * 1024
MAX_RESPONSE_LIMIT = 20 * 1024 * 1024


def _int_env(name, default, minimum=1, maximum=None):
    raw = (os.environ.get(name) or "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def max_transfer_bytes():
    return _int_env(
        "CURLGEN_MCP_MAX_TRANSFER",
        DEFAULT_MAX_TRANSFER,
        minimum=1,
        maximum=MAX_TRANSFER_LIMIT,
    )


def transfer_chunk_size():
    return _int_env(
        "CURLGEN_MCP_CHUNK_SIZE",
        DEFAULT_CHUNK_SIZE,
        minimum=MIN_CHUNK_SIZE,
        maximum=MAX_CHUNK_SIZE,
    )


def max_response_bytes():
    return _int_env(
        "CURLGEN_MCP_MAX_RESPONSE",
        DEFAULT_MAX_RESPONSE,
        minimum=1,
        maximum=MAX_RESPONSE_LIMIT,
    )


def _root_candidates():
    raw = (os.environ.get("CURLGEN_MCP_FILE_ROOTS") or "").strip()
    if raw:
        return [part for part in raw.split(os.pathsep) if part.strip()]
    return [str(Path.home()), str(Path.cwd())]


def allowed_roots():
    roots = []
    for candidate in _root_candidates():
        resolved = Path(candidate).expanduser().resolve(strict=False)
        if resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def resolve_local_path(value, must_exist=False):
    """Resolve ``value`` and ensure it stays below an allowed root."""
    if not value or not str(value).strip():
        raise ValueError("本地路径不能为空")
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve(strict=False)
    if not any(_is_within(path, root) for root in allowed_roots()):
        roots = ", ".join(str(root) for root in allowed_roots())
        raise ValueError(f"本地路径不在允许的根目录内（{roots}）: {path}")
    if must_exist and not path.is_file():
        raise ValueError(f"本地文件不存在或不是普通文件: {path}")
    return path


def read_local_file(value, max_bytes=None):
    path = resolve_local_path(value, must_exist=True)
    limit = max_transfer_bytes() if max_bytes is None else int(max_bytes)
    size = path.stat().st_size
    if size > limit:
        raise ValueError(f"文件大小 {size} 字节超过限制 {limit} 字节")
    return path.read_bytes()


def write_local_file(value, data, max_bytes=None, create_parents=True):
    limit = max_transfer_bytes() if max_bytes is None else int(max_bytes)
    size = len(data)
    if size > limit:
        raise ValueError(f"文件大小 {size} 字节超过限制 {limit} 字节")
    path = resolve_local_path(value)
    if create_parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(value):
    path = resolve_local_path(value, must_exist=True)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()