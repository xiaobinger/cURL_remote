import hashlib
import logging

logger = logging.getLogger(__name__)

def sort_params(params: dict, sign_param_name: str = "sign") -> str:
    """
    Java风格参数排序（键名不区分大小写排序）
    :param params: 参数字典 {'key1': 'value1', 'key2': 'value2'}
    :return: 排序后的字符串 key1=value1&key2=value2&
    """
    filtered = {k: v for k, v in params.items() if v is not None and str(v) != '' and str(k) != sign_param_name}
    sorted_items = sorted(filtered.items(), key=lambda x: x[0].upper())  # 不区分大小写排序
    return '&'.join([f"{k}={v}" for k, v in sorted_items]) + '&'

def sha256_hex(data: str) -> str:
    """
    Java风格SHA256加密（模仿MessageDigest.getInstance("SHA-256")）
    :param data: 要加密的字符串
    :return: 全大写的十六进制字符串
    """
    sha = hashlib.sha256()
    sha.update(data.encode('utf-8'))
    return sha.hexdigest().upper()

def encrypt(object_as_map: dict, key: str, sign_param_name: str = "sign") -> str:
    """
    完整签名实现（对应Java版本）
    :param object_as_map: 参数字典 {'param1': 'value1', ...}
    :param key: 签名密钥
    :return: 全大写的SHA256签名
    """
    # 生成排序后的参数字符串
    sorted_str = sort_params(object_as_map, sign_param_name)
    build_str = sorted_str + f"key={key}"
    logger.debug("Build string: %s", build_str)
    
    # SHA256加密并转大写十六进制
    encrypted = sha256_hex(build_str)
    return encrypted