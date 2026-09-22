"""通知发送器（端口自 script_monit_api 的 NotifierManager/DingDing/WeCom/Email）。

设计要点：
- 三个发送函数返回 (success: bool, detail: str)，由调用方统一记录，绝不因单渠道失败而崩溃。
- `send_via_channel(channel, message)` 是统一入口，自动按渠道类型分发，
  并从 `channel.get_decrypted_config()` 取明文配置（落库是密文）。
- 邮件优先使用渠道自带 SMTP 配置；未配 host 时回退到平台 EMAIL_* 配置。
"""
import base64
import hashlib
import hmac
import json
import time
import urllib.parse

import requests
from django.conf import settings
from django.core.mail import EmailMessage, get_connection


def send_dingtalk(webhook_url, secret, message, at_all=True, timeout=10):
    if not webhook_url:
        return False, "钉钉 webhook 未配置"
    timestamp = str(round(time.time() * 1000))
    secret_enc = (secret or "").encode("utf-8")
    string_to_sign = "{}\n{}".format(timestamp, secret or "")
    hmac_code = hmac.new(
        secret_enc, string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    url = "{}&timestamp={}&sign={}".format(webhook_url, timestamp, sign)
    payload = {
        "msgtype": "text",
        "text": {"content": message},
        "at": {"isAtAll": bool(at_all)},
    }
    try:
        resp = requests.post(
            url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json", "Charset": "utf-8"},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return False, "HTTP {}".format(resp.status_code)
        result = resp.json()
        if result.get("errcode") == 0:
            return True, "ok"
        return False, result.get("errmsg", "未知错误")
    except requests.RequestException as exc:
        return False, str(exc)


def send_wecom(webhook_url, message, mentioned_list=None, at_all=False, timeout=10):
    if not webhook_url:
        return False, "企业微信 webhook 未配置"
    # 构造 @成员列表：若开启 @all，在最前面插入 "@all"
    users = list(mentioned_list or [])
    if at_all and "@all" not in users:
        users.insert(0, "@all")
    payload = {
        "msgtype": "text",
        "text": {
            "content": message,
            "mentioned_list": users,
        },
    }
    try:
        resp = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json", "Charset": "utf-8"},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return False, "HTTP {}".format(resp.status_code)
        result = resp.json()
        if result.get("errcode") == 0:
            return True, "ok"
        return False, result.get("errmsg", "未知错误")
    except requests.RequestException as exc:
        return False, str(exc)


def send_email(message, receivers, subject="监控告警", config=None, timeout=15):
    if not receivers:
        return False, "未配置收件人"
    config = config or {}
    host = config.get("host")
    if host:
        port = config.get("port", 465)
        username = config.get("username")
        password = config.get("password")
        use_ssl = config.get("use_ssl", True)
        conn = get_connection(
            backend="django.core.mail.backends.smtp.EmailBackend",
            host=host,
            port=port,
            username=username,
            password=password,
            use_ssl=use_ssl,
            timeout=timeout,
        )
    else:
        # 回退到平台 EMAIL_* 配置
        conn = get_connection(timeout=timeout)
    try:
        email = EmailMessage(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=list(receivers),
            connection=conn,
        )
        email.send()
        return True, "ok"
    except Exception as exc:  # SMTPException / OSError 等
        return False, str(exc)


def _looks_like_cipher(v):
    """检测值是否仍为 Fernet 密文（gAAAAA 开头），用于识别密钥变更后未重加密的字段。"""
    return isinstance(v, str) and v.startswith("gAAAAA")


def _valid_webhook(v):
    return isinstance(v, str) and (v.startswith("http://") or v.startswith("https://"))


def send_via_channel(channel, message, subject="监控告警"):
    """统一入口：按渠道类型分发，自动取明文配置。返回 (success, detail)。"""
    cfg = channel.get_decrypted_config()
    ctype = channel.type
    if ctype == "DINGTALK":
        webhook_url = cfg.get("webhook_url")
        if _looks_like_cipher(webhook_url) or not _valid_webhook(webhook_url):
            return False, (
                "钉钉渠道「{}」配置已失效：Webhook 地址为密文或缺失"
                "（通常是 SECRET_KEY 变更后未重加密）。请到「通知渠道」页"
                "重新保存该渠道的 Webhook 地址与加签密钥，或使用明文重新配置。"
            ).format(channel.name)
        return send_dingtalk(
            webhook_url, cfg.get("secret"),
            message, at_all=cfg.get("at_all", True),
        )
    if ctype == "WECOM":
        webhook_url = cfg.get("webhook_url")
        if _looks_like_cipher(webhook_url) or not _valid_webhook(webhook_url):
            return False, (
                "企业微信渠道「{}」配置已失效：Webhook 地址为密文或缺失"
                "（通常是 SECRET_KEY 变更后未重加密）。请到「通知渠道」页"
                "重新保存该渠道的 Webhook 地址，或使用明文重新配置。"
            ).format(channel.name)
        return send_wecom(
            webhook_url, message,
            mentioned_list=cfg.get("mentioned_list"),
            at_all=cfg.get("at_all", False),
        )
    if ctype == "EMAIL":
        return send_email(
            message, cfg.get("receivers") or [], subject=subject, config=cfg,
        )
    return False, "未知渠道类型: {}".format(ctype)
