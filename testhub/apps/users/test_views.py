from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
import json
import re
from django.contrib.auth import get_user_model

User = get_user_model()


def _validate_phone(phone):
    """校验手机号格式"""
    if not phone:
        return False, '请输入手机号'
    if not re.match(r'^1[3-9]\d{9}$', phone):
        return False, '手机号格式不正确'
    return True, ''


@csrf_exempt
@require_http_methods(["POST"])
def test_register(request):
    try:
        data = json.loads(request.body)

        # 检查用户名是否已存在
        if User.objects.filter(username=data.get('username')).exists():
            return JsonResponse({
                'success': False,
                'error': '用户名已存在'
            }, status=400)

        # 手机号校验
        phone = data.get('phone', '').strip()
        valid, error = _validate_phone(phone)
        if not valid:
            return JsonResponse({'success': False, 'error': error}, status=400)

        # 检查手机号是否已被注册
        if User.objects.filter(phone=phone).exists():
            return JsonResponse({
                'success': False,
                'error': '该手机号已被注册'
            }, status=400)

        # 短信验证码校验
        verify_code = data.get('verify_code', '').strip()
        verify_code_token = data.get('verify_code_token', '').strip()
        if not verify_code or not verify_code_token:
            return JsonResponse({
                'success': False,
                'error': '请输入短信验证码'
            }, status=400)

        from .sms import validate_verify_code
        valid, error = validate_verify_code(phone, verify_code_token, verify_code)
        if not valid:
            return JsonResponse({'success': False, 'error': error}, status=400)

        # 创建用户
        user = User.objects.create_user(
            username=data.get('username'),
            email=data.get('email', ''),
            password=data.get('password'),
            first_name=data.get('first_name', ''),
            last_name=data.get('last_name', ''),
            phone=phone,
            department=data.get('department', ''),
            position=data.get('position', '')
        )

        # 生成 JWT token
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = RefreshToken.for_user(user)

        return JsonResponse({
            'success': True,
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'phone': user.phone,
                'first_name': user.first_name,
                'last_name': user.last_name
            },
            'access': str(refresh.access_token),
            'refresh': str(refresh),
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)