"""Java-compatible request signing (MD5 / SHA256).

The desktop app's backend builds the signature string as:

    sorted(k1=v1&k2=v2&...) + "&key=" + secret

Keys are sorted case-insensitively by their upper-cased form. Empty values,
``None`` and the signature field itself are excluded. Both algorithms return
upper-case hexadecimal.
"""

import hashlib


def _normalize_algo(algo: str) -> str:
    value = (algo or "MD5").strip().upper().replace("-", "")
    if value not in ("MD5", "SHA256"):
        raise ValueError(f"不支持的签名算法: {algo!r}（仅支持 MD5 / SHA256）")
    return value


def string_to_sign(params, sign_field="sign"):
    """Return the Java-style parameter string with a trailing ``&``.

    ``params`` may be a dict or an iterable of ``(key, value)`` pairs.
    """
    items = params.items() if hasattr(params, "items") else params
    kept = []
    for key, value in items:
        key = str(key)
        if key == str(sign_field) or value is None:
            continue
        text = str(value)
        if text == "":
            continue
        kept.append((key, text))
    kept.sort(key=lambda pair: pair[0].upper())
    return "&".join(f"{key}={value}" for key, value in kept) + "&"


def compute_sign(params, secret, algo="MD5", sign_field="sign"):
    """Compute the signature, or return ``None`` when no secret is configured."""
    if not secret:
        return None
    algorithm = _normalize_algo(algo)
    payload = (string_to_sign(params, sign_field) + f"key={secret}").encode("utf-8")
    if algorithm == "SHA256":
        return hashlib.sha256(payload).hexdigest().upper()
    return hashlib.md5(payload).hexdigest().upper()