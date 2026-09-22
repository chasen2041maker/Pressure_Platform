"""monitor 加密工具单元测试（不依赖 DB，纯函数级）。

回归背景（2026-07-27）：
监控通知渠道「编辑」时，后端 retrieve 会把 config 原样返回给前端。
当某个敏感字段是「旧 SECRET_KEY 加密的密文」而当前密钥解不开时，
旧实现 decrypt() 失败会原样返回密文（gAAAAA...），前端把密文填进
表单、保存时又二次加密，越修越坏；且测试发送时出现难懂的 Invalid URL。

修复：decrypt() 仅对疑似 Fernet 令牌（gAAAAA 前缀）尝试解密，解密失败
返回 None（非令牌明文原样返回，保持幂等）。decrypt_secrets 对敏感键
解密失败置 None，retrieve 不再泄漏密文。
"""
import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from apps.monitor.utils.crypto import decrypt, decrypt_secrets


def test_decrypt_returns_none_for_undecryptable_ciphertext():
    """疑似 Fernet 令牌但解不开（密钥变更）必须返回 None，而非泄漏密文。"""
    assert decrypt('gAAAAAzzz-this-is-not-a-valid-token') is None


def test_decrypt_is_idempotent_on_plaintext():
    """非令牌明文原样返回，保证对已明文存储的字段不破坏。"""
    assert decrypt('https://oapi.dingtalk.com/robot/send?access_token=abc') == \
        'https://oapi.dingtalk.com/robot/send?access_token=abc'
    assert decrypt('SEC123') == 'SEC123'
    assert decrypt('') == ''
    assert decrypt(None) is None


def test_decrypt_secrets_blanks_undecryptable_ciphertext_to_none():
    """敏感键的失效密文应转为 None（而非密文字符串）。"""
    cfg = {
        'webhook_url': 'gAAAAAxxx-invalid-webhook-token',
        'secret': 'gAAAAAyyy-invalid-secret-token',
        'at_all': True,
    }
    out = decrypt_secrets(cfg)
    assert out['webhook_url'] is None
    assert out['secret'] is None
    assert out['at_all'] is True  # 非敏感键保持原值


def test_decrypt_secrets_keeps_plaintext_secret():
    """正常明文敏感字段保持原值（幂等）。"""
    cfg = {
        'webhook_url': 'https://oapi.dingtalk.com/robot/send?access_token=abc',
        'secret': 'SEC123',
    }
    out = decrypt_secrets(cfg)
    assert out['webhook_url'] == 'https://oapi.dingtalk.com/robot/send?access_token=abc'
    assert out['secret'] == 'SEC123'


def test_decrypt_secrets_nested_dict():
    """嵌套 dict 中的失效密文同样置 None。"""
    cfg = {'smtp': {'password': 'gAAAAApassword-token-invalid'}}
    out = decrypt_secrets(cfg)
    assert out['smtp']['password'] is None
