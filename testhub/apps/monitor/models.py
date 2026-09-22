"""监控中心数据模型。

4 张核心表：
- MonitorTarget        监控目标（被探测的系统/接口/设备）
- MonitorCheckLog      探测记录（每次探测的结果）
- NotificationChannel  通知渠道（钉钉/企业微信/邮件）
- AlertEvent           告警记录（一次失败 episode）

字段命名对齐 api_testing 范式（status / next_check_at / should_run_now() /
calculate_next_run() / created_at / updated_at），保证调度器兼容、团队零认知成本。
"""
import time

from django.conf import settings
from django.db import models
from django.utils import timezone
from datetime import timedelta

from .utils.crypto import decrypt_secrets, encrypt_secrets


def _default_alert_repeat_interval():
    """全局可配置：自动监测持续 DOWN 时重提醒间隔（分钟），来源 settings。"""
    return getattr(settings, 'MONITOR_DEFAULT_ALERT_REPEAT_INTERVAL', 30)


def _default_manual_alert_cooldown():
    """全局可配置：手动「立即检测」触发告警后的冷却（分钟），来源 settings。"""
    return getattr(settings, 'MONITOR_DEFAULT_MANUAL_ALERT_COOLDOWN', 5)


# ---------- 枚举常量 ----------
class TargetType:
    LOGIN = 'LOGIN'      # 登录态可用性
    HTTP = 'HTTP'        # 接口存活
    ONLINE = 'ONLINE'    # 在线率（业务接口断言）
    DOCKER = 'DOCKER'    # 容器状态（TCP 探活）
    SL651 = 'SL651'      # 遥测链路（TCP + MySQL）
    CHOICES = (
        (LOGIN, '登录可用性'),
        (HTTP, '接口存活'),
        (ONLINE, '在线率'),
        (DOCKER, '容器状态'),
        (SL651, '遥测链路'),
    )


class TargetStatus:
    UP = 'UP'
    DOWN = 'DOWN'
    UNKNOWN = 'UNKNOWN'
    CHOICES = (
        (UP, '正常'),
        (DOWN, '异常'),
        (UNKNOWN, '未知'),
    )


class ChannelType:
    DINGTALK = 'DINGTALK'
    WECOM = 'WECOM'
    EMAIL = 'EMAIL'
    CHOICES = (
        (DINGTALK, '钉钉'),
        (WECOM, '企业微信'),
        (EMAIL, '邮件'),
    )


class AlertLevel:
    CRITICAL = 'CRITICAL'
    WARNING = 'WARNING'
    CHOICES = (
        (CRITICAL, '严重'),
        (WARNING, '警告'),
    )


class AlertStatus:
    FIRING = 'FIRING'
    ACKED = 'ACKED'
    RESOLVED = 'RESOLVED'
    CHOICES = (
        (FIRING, '告警中'),
        (ACKED, '已认领'),
        (RESOLVED, '已恢复'),
    )


class MonitorTarget(models.Model):
    """监控目标：被探测的系统/接口/设备。"""

    name = models.CharField(max_length=200, unique=True, verbose_name='目标名称', db_comment='目标名称')
    type = models.CharField(max_length=20, choices=TargetType.CHOICES, verbose_name='探测类型', db_comment='探测类型')
    url = models.CharField(max_length=500, blank=True, verbose_name='URL', db_comment='URL')
    method = models.CharField(max_length=10, default='GET', verbose_name='请求方法', db_comment='请求方法')
    host = models.CharField(max_length=200, blank=True, verbose_name='主机', db_comment='主机')
    port = models.IntegerField(null=True, blank=True, verbose_name='端口', db_comment='端口')
    check_config = models.JSONField(default=dict, verbose_name='探测配置', db_comment='探测配置')
    interval_seconds = models.IntegerField(default=60, verbose_name='轮询间隔(秒)', db_comment='轮询间隔(秒)')
    alert_threshold = models.IntegerField(default=3, verbose_name='告警阈值(连续失败次数)', db_comment='告警阈值(连续失败次数)')
    alert_repeat_interval = models.IntegerField(default=_default_alert_repeat_interval, verbose_name='自动重提醒间隔(分钟)', db_comment='自动重提醒间隔(分钟)')
    manual_alert_cooldown = models.IntegerField(default=_default_manual_alert_cooldown, verbose_name='手动检测告警冷却(分钟)', db_comment='手动检测告警冷却(分钟)')
    enabled = models.BooleanField(default=True, verbose_name='启用', db_comment='启用')
    status = models.CharField(
        max_length=10, choices=TargetStatus.CHOICES,
        default=TargetStatus.UNKNOWN, verbose_name='当前状态',
    
    db_comment='当前状态',)
    last_check_at = models.DateTimeField(null=True, blank=True, verbose_name='最近检测时间', db_comment='最近检测时间')
    consecutive_failures = models.IntegerField(default=0, verbose_name='连续失败次数', db_comment='连续失败次数')
    next_check_at = models.DateTimeField(null=True, blank=True, verbose_name='下次检测时间', db_comment='下次检测时间')
    primary_channels = models.ManyToManyField(
        'NotificationChannel', blank=True,
        related_name='primary_targets', verbose_name='一级通道(主)',
    )
    secondary_channels = models.ManyToManyField(
        'NotificationChannel', blank=True,
        related_name='secondary_targets', verbose_name='二级通道(备)',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='monitor_targets', verbose_name='创建人',
    
    db_comment='创建人',)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间', db_comment='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间', db_comment='更新时间')

    class Meta:
        db_table_comment = '监控目标'
        db_table = 'monitor_targets'
        verbose_name = '监控目标'
        verbose_name_plural = '监控目标'
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def calculate_next_run(self):
        self.next_check_at = timezone.now() + timedelta(seconds=self.interval_seconds or 60)

    def should_run_now(self):
        return self.enabled and (
            self.next_check_at is None or self.next_check_at <= timezone.now()
        )

    def save(self, *args, **kwargs):
        # 幂等加密：先 decrypt（明文原样返回）再 encrypt，保证始终单层密文
        self.check_config = encrypt_secrets(decrypt_secrets(self.check_config or {}))
        super().save(*args, **kwargs)

    def get_decrypted_check_config(self):
        return decrypt_secrets(self.check_config or {})

    def run_check(self, manual=False):
        """执行一次探测：跑探针 → 落 CheckLog → 回写状态/延迟/连续失败/下次时间。

        manual=True 表示来自「立即检测」手动触发（默认 False，来自定时调度）。
        该标志透传给 maybe_alert，用于区分手动/自动两套告警冷却策略。

        返回新建的 MonitorCheckLog 实例。任何探针异常都不会抛出（保证调度稳定）。
        """
        from .utils.probes import run_probe, ProbeResult

        cfg = self.get_decrypted_check_config()
        try:
            result = run_probe(self, cfg)
        except Exception as exc:  # 探针本身不应抛，但兜底防崩溃
            result = ProbeResult(False, f"探测异常: {exc}")

        status = TargetStatus.UP if result.ok else TargetStatus.DOWN
        self.status = status
        self.last_check_at = timezone.now()
        if result.ok:
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1
        self.calculate_next_run()
        self.save(update_fields=[
            "status", "last_check_at", "consecutive_failures", "next_check_at",
        ])

        log = MonitorCheckLog.objects.create(
            target=self,
            status=status,
            latency_ms=result.latency_ms,
            http_status=result.http_status,
            error_message="" if result.ok else result.message,
            detail=result.detail,
            triggered_alert=False,
        )
        # 告警钩子：连续失败达阈值时创建 AlertEvent 并分发通知（#7 实现）
        self.maybe_alert(log, manual=manual)
        return log

    @staticmethod
    def _alert_ever_sent(alert):
        """episode 是否已成功发出过通知：send_detail 中存在 ok=True 即视为已发。"""
        for item in (alert.send_detail or []):
            if item.get('ok'):
                return True
        return False

    def maybe_alert(self, log, manual=False):
        """告警触发：基于连续失败计数的 episode 模式 + 友好冷却重提醒（A+B 设计）。

        - DOWN 且连续失败达阈值、且无未恢复告警 → 新建 AlertEvent(FIRING) 并分发一次通知。
        - DOWN 且已有未恢复告警，但从未成功通知过（如首发时无渠道/渠道故障）→ 补发。
        - DOWN 且已有未恢复告警且已成功通知 → 按来源走冷却重提醒：
            · manual=True（立即检测）：超过 manual_alert_cooldown(默认5min) 即重推，防狂点刷屏；
            · manual=False（自动监测）：超过 alert_repeat_interval(默认30min) 才重推，持续 DOWN 仍周期提醒。
        - UP 且有未恢复告警 → 自动 RESOLVED 并补发恢复通知。
        """
        open_alert = AlertEvent.objects.filter(
            target=self, status__in=[AlertStatus.FIRING, AlertStatus.ACKED]
        ).first()

        if log.status == TargetStatus.DOWN:
            if self.consecutive_failures >= self.alert_threshold:
                if not open_alert:
                    alert = AlertEvent.objects.create(
                        target=self,
                        level=AlertLevel.CRITICAL,
                        message=log.error_message or '目标不可用',
                        status=AlertStatus.FIRING,
                        last_triggered_at=timezone.now(),
                    )
                    sent = self._dispatch_alert(alert, recovered=False)
                    alert.send_detail = sent
                    alert.notify_count = 1
                    alert.last_notified_at = timezone.now()
                    alert.save(update_fields=['send_detail', 'notify_count', 'last_notified_at'])
                    log.triggered_alert = True
                    log.save(update_fields=['triggered_alert'])
                elif not self._alert_ever_sent(open_alert):
                    # 同一 episode 已存在但从未成功通知（首发时无渠道/渠道故障），
                    # 补发一次，避免“绑渠道晚于首次触发”导致永远收不到告警。
                    sent = self._dispatch_alert(open_alert, recovered=False)
                    open_alert.send_detail = (open_alert.send_detail or []) + sent
                    open_alert.notify_count = (open_alert.notify_count or 0) + 1
                    open_alert.last_notified_at = timezone.now()
                    open_alert.save(update_fields=['send_detail', 'notify_count', 'last_notified_at'])
                    log.triggered_alert = True
                    log.save(update_fields=['triggered_alert'])
                else:
                    # 已存在且已成功通知的 episode：按来源走冷却重提醒（友好设计）
                    cooldown_min = self.manual_alert_cooldown if manual else self.alert_repeat_interval
                    last = open_alert.last_notified_at
                    if last is None or (timezone.now() - last) >= timedelta(minutes=cooldown_min):
                        dur_min = int((timezone.now() - open_alert.first_triggered_at).total_seconds() / 60)
                        n = (open_alert.notify_count or 0) + 1
                        open_alert.message = '{}（第 {} 次提醒，已持续 {} 分钟）'.format(
                            log.error_message or '目标不可用', n, dur_min)
                        sent = self._dispatch_alert(open_alert, recovered=False)
                        open_alert.send_detail = (open_alert.send_detail or []) + sent
                        open_alert.notify_count = n
                        open_alert.last_notified_at = timezone.now()
                        open_alert.last_triggered_at = timezone.now()
                        open_alert.save(update_fields=[
                            'send_detail', 'notify_count', 'last_notified_at',
                            'last_triggered_at', 'message'])
                        log.triggered_alert = True
                        log.save(update_fields=['triggered_alert'])
            return open_alert

        # 恢复：UP
        if open_alert:
            sent = self._dispatch_alert(open_alert, recovered=True)
            open_alert.send_detail = (open_alert.send_detail or []) + sent
            open_alert.status = AlertStatus.RESOLVED
            open_alert.resolved_at = timezone.now()
            open_alert.save(update_fields=['send_detail', 'status', 'resolved_at'])
            log.triggered_alert = True
            log.save(update_fields=['triggered_alert'])
        return None

    def _dispatch_alert(self, alert, recovered=False):
        """对目标绑定的启用渠道分发告警/恢复消息，返回发送结果列表（内聚在 send_detail）。

        渠道分级（一级优先、二级容灾）：
        - 先向「一级通道(主)」全部启用渠道推送；
        - 若一级通道全部失败（或一级未配置任何渠道），自动切换「二级通道(备)」推送；
        - 一级只要有任一成功即视为送达，不再走二级（避免重复打扰）。
        每条发送结果带 level 字段（primary/secondary）便于前端回显与追溯。
        """
        from .utils.notifiers import send_via_channel

        type_label = dict(TargetType.CHOICES).get(self.type, self.type)
        cfg = self.get_decrypted_check_config()
        target_url = (
            cfg.get('login_url')
            or cfg.get('url')
            or self.url
            or '-'
        )
        now_str = timezone.localtime(timezone.now()).strftime('%Y-%m-%d %H:%M:%S')

        # 取最近一次探测记录的响应详情
        latest_log = self.checks.order_by('-checked_at').first()
        resp_body = '-'
        if latest_log and latest_log.detail:
            raw = latest_log.detail.get('body') or latest_log.detail.get('resp') or ''
            if isinstance(raw, str) and raw.strip():
                resp_body = raw[:200] + '...' if len(raw) > 200 else raw
            elif isinstance(raw, (dict, list)):
                import json
                raw_str = json.dumps(raw, ensure_ascii=False)
                resp_body = raw_str[:200] + '...' if len(raw_str) > 200 else raw_str

        if recovered:
            # 计算异常持续时长
            duration = ''
            if alert.first_triggered_at:
                delta = timezone.now() - alert.first_triggered_at
                minutes = int(delta.total_seconds() // 60)
                if minutes < 1:
                    duration = '不足 1 分钟'
                elif minutes < 60:
                    duration = f'约 {minutes} 分钟'
                else:
                    duration = f'约 {minutes // 60} 小时 {minutes % 60} 分钟'
            title = '【监控中心】已恢复：{}'.format(self.name)
            body = (
                '🟢 恢复：\n'
                '-----> 接口已恢复 <-----\n\n'
                '【监控时间】：{}\n'
                '【接口名称】：{}\n'
                '【监测类型】：{}\n'
                '【请求地址】：{}\n'
                '【异常时长】：{}'
            ).format(now_str, self.name, type_label, target_url, duration)
        else:
            title = '【监控中心】告警：{}'.format(self.name)
            body = (
                '🔴 告警：\n'
                '-----> 接口监控异常 <-----\n\n'
                '【监控时间】：{}\n'
                '【接口名称】：{}\n'
                '【监测类型】：{}\n'
                '【请求地址】：{}\n'
                '【失败次数】：第 {} 次连续失败\n'
                '【错误信息】：{}\n'
                '【响应内容】：{}'
            ).format(
                now_str,
                self.name,
                type_label,
                target_url,
                self.consecutive_failures,
                (alert.message or '-')[:200],
                resp_body,
            )

        results = []
        # 一级通道（主）优先推送
        primary_channels = list(self.primary_channels.filter(enabled=True))
        primary_ok = False
        for ch in primary_channels:
            ok, msg = send_via_channel(ch, message=body, subject=title)
            if ok:
                primary_ok = True
            results.append({
                'channel': ch.name,
                'type': ch.type,
                'ok': ok,
                'detail': msg,
                'level': 'primary',
                'recovered': recovered,
                'sent_at': timezone.now().isoformat(),
            })

        # 一级通道全部失败（或一级未配置任何渠道）→ 自动切换二级通道（备）推送
        if not primary_ok:
            secondary_channels = list(self.secondary_channels.filter(enabled=True))
            if secondary_channels:
                for ch in secondary_channels:
                    ok, msg = send_via_channel(ch, message=body, subject=title)
                    results.append({
                        'channel': ch.name,
                        'type': ch.type,
                        'ok': ok,
                        'detail': msg,
                        'level': 'secondary',
                        'fallback': True,
                        'recovered': recovered,
                        'sent_at': timezone.now().isoformat(),
                    })
        return results


class MonitorCheckLog(models.Model):
    """探测记录：每次探测的结果。"""

    target = models.ForeignKey(
        MonitorTarget, on_delete=models.CASCADE,
        related_name='checks', verbose_name='监控目标',
    
    db_comment='监控目标',)
    status = models.CharField(
        max_length=10, choices=TargetStatus.CHOICES,
        default=TargetStatus.UNKNOWN, verbose_name='结果',
    
    db_comment='结果',)
    latency_ms = models.IntegerField(null=True, blank=True, verbose_name='延迟(ms)', db_comment='延迟(ms)')
    http_status = models.IntegerField(null=True, blank=True, verbose_name='HTTP状态码', db_comment='HTTP状态码')
    error_message = models.TextField(blank=True, verbose_name='错误信息', db_comment='错误信息')
    detail = models.JSONField(default=dict, verbose_name='响应摘要', db_comment='响应摘要')
    triggered_alert = models.BooleanField(default=False, verbose_name='是否触发告警', db_comment='是否触发告警')
    checked_at = models.DateTimeField(auto_now_add=True, verbose_name='检测时间', db_comment='检测时间')

    class Meta:
        db_table_comment = '探测记录'
        db_table = 'monitor_check_logs'
        verbose_name = '探测记录'
        verbose_name_plural = '探测记录'
        ordering = ['-checked_at']
        indexes = [
            models.Index(fields=['target', 'checked_at']),
        ]

    def __str__(self):
        return f'{self.target_id}-{self.status}-{self.checked_at}'


class NotificationChannel(models.Model):
    """通知渠道：钉钉/企业微信/邮件。"""

    name = models.CharField(max_length=100, unique=True, verbose_name='渠道名称', db_comment='渠道名称')
    type = models.CharField(max_length=20, choices=ChannelType.CHOICES, verbose_name='渠道类型', db_comment='渠道类型')
    config = models.JSONField(default=dict, verbose_name='渠道配置', db_comment='渠道配置')
    enabled = models.BooleanField(default=True, verbose_name='启用', db_comment='启用')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='monitor_channels', verbose_name='创建人',
    
    db_comment='创建人',)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间', db_comment='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间', db_comment='更新时间')

    class Meta:
        db_table_comment = '通知渠道'
        db_table = 'monitor_channels'
        verbose_name = '通知渠道'
        verbose_name_plural = '通知渠道'
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # 幂等加密：先 decrypt（明文原样返回）再 encrypt，保证始终单层密文
        self.config = encrypt_secrets(decrypt_secrets(self.config or {}))
        super().save(*args, **kwargs)

    def get_decrypted_config(self):
        return decrypt_secrets(self.config or {})


class AlertEvent(models.Model):
    """告警记录：一次失败 episode（多渠道发送结果内聚在 send_detail）。"""

    target = models.ForeignKey(
        MonitorTarget, on_delete=models.CASCADE,
        related_name='alerts', verbose_name='监控目标',
    
    db_comment='监控目标',)
    level = models.CharField(
        max_length=20, choices=AlertLevel.CHOICES,
        default=AlertLevel.CRITICAL, verbose_name='级别',
    
    db_comment='级别',)
    message = models.TextField(verbose_name='告警内容', db_comment='告警内容')
    status = models.CharField(
        max_length=20, choices=AlertStatus.CHOICES,
        default=AlertStatus.FIRING, verbose_name='状态',
    
    db_comment='状态',)
    first_triggered_at = models.DateTimeField(auto_now_add=True, verbose_name='首次触发', db_comment='首次触发')
    last_triggered_at = models.DateTimeField(null=True, blank=True, verbose_name='最近触发', db_comment='最近触发')
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name='恢复时间', db_comment='恢复时间')
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='monitor_alerts_acked', verbose_name='认领人',
    
    db_comment='认领人',)
    acknowledged_at = models.DateTimeField(null=True, blank=True, verbose_name='认领时间', db_comment='认领时间')
    send_detail = models.JSONField(default=list, verbose_name='发送结果', db_comment='发送结果')
    notify_count = models.IntegerField(default=0, verbose_name='已通知次数', db_comment='已通知次数')
    last_notified_at = models.DateTimeField(null=True, blank=True, verbose_name='最近通知时间', db_comment='最近通知时间')

    class Meta:
        db_table_comment = '告警记录'
        db_table = 'monitor_alerts'
        verbose_name = '告警记录'
        verbose_name_plural = '告警记录'
        ordering = ['-last_triggered_at', '-first_triggered_at']

    def __str__(self):
        return f'{self.target_id}-{self.status}'
