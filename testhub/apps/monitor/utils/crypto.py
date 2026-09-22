"""密钥字段落库加密工具。

基于 Django 的 SECRET_KEY 派生 Fernet 密钥，对监控目标/通知渠道中的
敏感子键（password/token/secret/webhook 等）做对称加密，避免生产账号明文入库。
"""
import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings

# 需要加密/掩码的敏感字段名（大小写不敏感）
SECRET_KEYS = {
    'password', 'token', 'secret', 'webhook',
    'access_key', 'secret_key', 'api_key', 'passwd',
}

# 子串匹配，覆盖 webhook_url / secret_key / access_key 等变体
_SECRET_SUBSTRINGS = (
    'password', 'passwd', 'secret', 'token', 'webhook',
    'apikey', 'accesskey', 'privatekey',
)


def is_secret_key(key):
    kl = (key or '').lower()
    return any(s in kl for s in _SECRET_SUBSTRINGS)


def _get_fernet():
    # 由 SECRET_KEY 派生出 32 字节的 Fernet 密钥（sha256 -> urlsafe base64）
    digest = hashlib.sha256(settings.SECRET_KEY.encode('utf-8')).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt(plaintext):
    """加密字符串，返回 str；非 str/None 原样返回。"""
    if plaintext is None or not isinstance(plaintext, str):
        return plaintext
    return _get_fernet().encrypt(plaintext.encode('utf-8')).decode('utf-8')


def decrypt(token):
    """解密字符串。

    - 非 str / None 原样返回。
    - 仅对疑似 Fernet 令牌（以 ``gAAAAA`` 开头）尝试解密；
      非令牌（明文）原样返回，保持对已明文存储字段的幂等。
    - 疑似令牌但解密失败（如 SECRET_KEY 变更后遗留的旧密文）
      返回 ``None``，避免把密文泄漏到 retrieve / 编辑表单
      （否则前端会二次加密，或测试发送时出现 Invalid URL）。
    """
    if token is None or not isinstance(token, str):
        return token
    if not token.startswith('gAAAAA'):
        return token
    try:
        return _get_fernet().decrypt(token.encode('utf-8')).decode('utf-8')
    except Exception:
        return None


def encrypt_secrets(data):
    """递归加密 dict 中的敏感子键（值转密文）。"""
    if not isinstance(data, dict):
        return data
    out = {}
    for k, v in data.items():
        kl = k.lower()
        if isinstance(v, dict):
            out[k] = encrypt_secrets(v)
        elif is_secret_key(k) and v not in (None, ''):
            out[k] = encrypt(str(v))
        else:
            out[k] = v
    return out


def decrypt_secrets(data):
    """递归解密 dict 中的敏感子键（密文转明文）。"""
    if not isinstance(data, dict):
        return data
    out = {}
    for k, v in data.items():
        kl = k.lower()
        if isinstance(v, dict):
            out[k] = decrypt_secrets(v)
        elif is_secret_key(k) and isinstance(v, str):
            out[k] = decrypt(v)
        else:
            out[k] = v
    return out
