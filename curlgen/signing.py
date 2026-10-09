"""签名计算"""
import curlgen.sign_utils as signUtil
import hashlib


def compute_sign(params: dict, secret_key, sign_type="MD5", sign_param_name="sign"):
    """计算签名。params 以 dict 传入，如 {'key1': 'value1', 'key2': 'value2'}"""
    if not secret_key:
        return None
    if sign_type == "SHA256":
        return signUtil.encrypt(params, secret_key, sign_param_name)
    sign_str = signUtil.sort_params(params, sign_param_name) + f"key={secret_key}"
    return hashlib.md5(sign_str.encode("utf-8")).hexdigest().upper()
