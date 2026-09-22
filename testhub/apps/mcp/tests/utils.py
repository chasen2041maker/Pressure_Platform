# -*- coding: utf-8 -*-
"""MCP 测试公共工具：构造带认证头的伪 Context。"""


class FakeRequest:
    def __init__(self, headers):
        # 与 Starlette Headers 语义一致：小写键
        self.headers = {k.lower(): v for k, v in headers.items()}


class FakeRequestContext:
    def __init__(self, request):
        self.request = request


class FakeContext:
    def __init__(self, headers=None):
        self.request_context = FakeRequestContext(FakeRequest(headers or {}))


def ctx_with_jwt(user):
    from rest_framework_simplejwt.tokens import RefreshToken
    access = str(RefreshToken.for_user(user).access_token)
    return FakeContext({'Authorization': f'Bearer {access}'}), access


def ctx_with_api_key(user):
    from rest_framework.authtoken.models import Token
    token, _ = Token.objects.get_or_create(user=user)
    return FakeContext({'x-mcp-api-key': token.key}), token.key
