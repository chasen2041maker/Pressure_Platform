"""危险操作 preview → confirm 两段式机制。

- preview：创建 McpPendingConfirm（status=pending），签发带时间戳的令牌
- confirm：TimestampSigner 无状态校验（5 分钟 max_age）+ DB 状态双重验证，
  通过后执行动作并置为 consumed（单次消费）
- 人工审批模式（MCP_HUMAN_APPROVAL=True）：confirm 不直接执行，
  标记 awaiting_human 等待控制台批准，Agent 轮询 get_approval_status 取结果
- 监控页批准：服务端直接触发动作执行（approved）
- 监控页拒绝：令牌作废（rejected），后续 confirm 返回明确错误

令牌不落 Redis，用 django.core.signing 实现无状态 + 防篡改，
DB 记录承载状态机与监控可视化，两者互补。
"""
import logging

from django.conf import settings
from django.core import signing
from django.utils import timezone

from .actions import ACTION_REGISTRY, McpActionError, build_preview
from .models import CONFIRM_TOKEN_TTL_SECONDS, McpPendingConfirm

logger = logging.getLogger(__name__)

_SALT = 'apps.mcp.confirm-token'


class ConfirmError(Exception):
    """confirm 校验失败（面向 Agent 的可读错误）。"""


def _human_approval_enabled() -> bool:
    return bool(getattr(settings, 'MCP_HUMAN_APPROVAL', False))


def notify_pending_approval(pending: McpPendingConfirm):
    """人工审批提醒：复用 monitor 模块已启用的通知渠道。

    未配置任何渠道或发送失败均静默降级（仅记日志），不阻断主流程。
    """
    try:
        from apps.monitor.models import NotificationChannel
        from apps.monitor.utils.notifiers import send_via_channel

        channels = NotificationChannel.objects.filter(enabled=True)
        if not channels.exists():
            logger.debug('MCP 审批通知跳过：未配置启用的通知渠道')
            return
        message = (
            f'【TestHub MCP 危险操作待审批】\n'
            f'发起人：{pending.user.username}\n'
            f'操作：{pending.tool_name}\n'
            f'影响：{pending.preview}\n'
            f'过期时间：{timezone.localtime(pending.expires_at):%Y-%m-%d %H:%M:%S}\n'
            f'请到「MCP 控制台 → 待确认」批准或拒绝。'
        )
        for channel in channels:
            ok, detail = send_via_channel(channel, message, subject='MCP 危险操作待审批')
            if not ok:
                logger.warning('MCP 审批通知发送失败 channel=%s: %s', channel.name, detail)
    except Exception as exc:  # noqa: BLE001 - 通知失败不阻断审批流程
        logger.debug('MCP 审批通知发送异常: %s', exc)


def create_preview(tool_name: str, arguments: dict, user) -> dict:
    """创建待确认记录并返回 preview 结果（供 preview_* 工具使用）。"""
    preview_text = build_preview(tool_name, arguments, user)
    pending = McpPendingConfirm.objects.create(
        user=user,
        tool_name=tool_name,
        arguments=arguments,
        preview=preview_text,
        status='pending',
        expires_at=McpPendingConfirm.default_expires_at(),
    )
    signer = signing.TimestampSigner(salt=_SALT)
    token = signer.sign(str(pending.id))
    if _human_approval_enabled():
        notify_pending_approval(pending)
    return {
        'preview': preview_text,
        'confirm_token': token,
        'confirm_tool': tool_name,
        'expires_in_seconds': CONFIRM_TOKEN_TTL_SECONDS,
    }


def verify_pending(confirm_token: str, user) -> McpPendingConfirm:
    """校验 confirm 令牌并返回待确认记录；任何异常抛 ConfirmError。"""
    if not confirm_token:
        raise ConfirmError('缺少 confirm_token')
    try:
        signed_value = signing.TimestampSigner(salt=_SALT).unsign(
            confirm_token, max_age=CONFIRM_TOKEN_TTL_SECONDS)
    except signing.SignatureExpired:
        raise ConfirmError('confirm_token 已过期（5 分钟有效），请重新调用 preview') from None
    except signing.BadSignature:
        raise ConfirmError('confirm_token 无效或已被篡改') from None

    pending = McpPendingConfirm.objects.filter(id=int(signed_value)).first()
    if pending is None:
        raise ConfirmError('confirm_token 对应的操作不存在')
    if pending.user_id != user.id:
        raise ConfirmError('无权确认其他用户发起的操作')
    if pending.status == 'rejected':
        raise ConfirmError('该操作已被管理员拒绝')
    if pending.status == 'consumed':
        raise ConfirmError('该操作已被执行，不可重复确认')
    if pending.status == 'approved':
        raise ConfirmError('该操作已由管理员批准执行，无需重复确认')
    if pending.is_expired:
        pending.status = 'expired'
        pending.save(update_fields=['status', 'updated_at'])
        raise ConfirmError('该操作已过期，请重新调用 preview')
    return pending


def consume_pending(pending: McpPendingConfirm, user) -> dict:
    """执行待确认动作（confirm_* 工具入口），单次消费。

    人工审批模式（MCP_HUMAN_APPROVAL=True）下不直接执行，标记
    awaiting_human 后返回等待指示，由控制台批准后经 approve_pending 执行。
    """
    if _human_approval_enabled():
        if not pending.awaiting_human:
            pending.awaiting_human = True
            pending.save(update_fields=['awaiting_human', 'updated_at'])
        return {
            'status': 'awaiting_approval',
            'pending_id': pending.id,
            'hint': '人工审批模式已开启：该操作已转入 TestHub「MCP 控制台」等待批准，'
                    '请稍后调用 get_approval_status 轮询审批结果',
        }
    action = ACTION_REGISTRY.get(pending.tool_name)
    if action is None:
        raise ConfirmError(f'未知的待确认操作: {pending.tool_name}')
    try:
        result = action(pending.arguments, user)
    except McpActionError as e:
        raise ConfirmError(str(e)) from e
    pending.status = 'consumed'
    pending.result = result
    pending.save(update_fields=['status', 'result', 'updated_at'])
    return result


def approve_pending(pending: McpPendingConfirm, operator) -> dict:
    """监控页手动批准：服务端触发动作执行。"""
    if pending.status != 'pending' or pending.is_expired:
        raise ConfirmError(f'当前状态({pending.status})不可批准')
    action = ACTION_REGISTRY.get(pending.tool_name)
    if action is None:
        raise ConfirmError(f'未知的待确认操作: {pending.tool_name}')
    try:
        result = action(pending.arguments, pending.user)
    except McpActionError as e:
        raise ConfirmError(str(e)) from e
    pending.status = 'approved'
    pending.result = {**(result or {}), 'approved_by': operator.username}
    pending.save(update_fields=['status', 'result', 'updated_at'])
    return result


def reject_pending(pending: McpPendingConfirm, operator):
    """监控页拒绝：令牌作废。"""
    if pending.status != 'pending':
        raise ConfirmError(f'当前状态({pending.status})不可拒绝')
    pending.status = 'rejected'
    pending.result = {'rejected_by': operator.username,
                      'rejected_at': timezone.now().isoformat()}
    pending.save(update_fields=['status', 'result', 'updated_at'])
    return pending


def _unsign_token(confirm_token: str):
    """解签 confirm 令牌；过期返回 None，无效/篡改抛 ConfirmError。"""
    if not confirm_token:
        raise ConfirmError('缺少 confirm_token')
    try:
        signed_value = signing.TimestampSigner(salt=_SALT).unsign(
            confirm_token, max_age=CONFIRM_TOKEN_TTL_SECONDS)
    except signing.SignatureExpired:
        return None
    except signing.BadSignature:
        raise ConfirmError('confirm_token 无效或已被篡改') from None
    return signed_value


def query_approval_status(confirm_token: str, user) -> dict:
    """轮询人工审批状态（get_approval_status 工具入口）。

    与 verify_pending 不同：终态（已批准/已拒绝/已过期）不抛错，
    而是返回状态与结果摘要，供 Agent 反复轮询。
    """
    signed_value = _unsign_token(confirm_token)
    if signed_value is None:
        return {'status': 'expired', 'hint': '令牌已过期（5 分钟有效），请重新调用 preview'}

    pending = McpPendingConfirm.objects.filter(id=int(signed_value)).first()
    if pending is None:
        raise ConfirmError('confirm_token 对应的操作不存在')
    if pending.user_id != user.id:
        raise ConfirmError('无权查询其他用户发起的操作')

    if pending.status in ('approved', 'consumed'):
        return {'status': 'approved', 'result': pending.result or {}}
    if pending.status == 'rejected':
        return {'status': 'rejected', 'result': pending.result or {}}
    if pending.is_expired:
        pending.status = 'expired'
        pending.save(update_fields=['status', 'updated_at'])
        return {'status': 'expired', 'hint': '操作已过期，请重新调用 preview'}
    remaining = int((pending.expires_at - timezone.now()).total_seconds())
    return {
        'status': 'awaiting_approval' if pending.awaiting_human else 'pending',
        'preview': pending.preview,
        'expires_in_seconds': max(remaining, 0),
        'hint': '等待人工审批，请稍后再次轮询' if pending.awaiting_human else '尚未确认执行',
    }
