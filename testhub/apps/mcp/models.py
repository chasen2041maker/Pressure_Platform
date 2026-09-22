"""MCP Server 数据模型。

遵循项目既有范式：显式 db_table + db_table_comment + verbose_name，
所有字段带 db_comment，JSONField 必带 default。

两张表均为新增，不影响任何现有表结构：
- McpCallLog：MCP 工具调用日志（监控页实时展示）
- McpPendingConfirm：危险操作 preview→confirm 待确认记录
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

User = get_user_model()

#: confirm 令牌有效期（秒），与文档约定一致：5 分钟
CONFIRM_TOKEN_TTL_SECONDS = 300


class McpCallLog(models.Model):
    """MCP 工具调用日志。

    参数存哈希摘要（args_digest）+ 脱敏截断文本（args_brief），
    兼顾审计回溯与表体积控制；超过 7 天的记录在写入时节流清理。
    """

    STATUS_CHOICES = [
        ('success', '成功'),
        ('error', '失败'),
        ('denied', '拒绝'),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='mcp_call_logs', verbose_name='调用用户', db_comment='调用用户')
    tool_name = models.CharField(max_length=100, verbose_name='工具名称', db_comment='工具名称')
    args_digest = models.CharField(max_length=64, blank=True, default='',
                                   verbose_name='参数摘要', db_comment='参数哈希摘要(不存原始参数)')
    args_brief = models.TextField(blank=True, default='',
                                  verbose_name='参数详情', db_comment='脱敏截断后的参数JSON(供审计回溯)')
    client_name = models.CharField(max_length=100, blank=True, default='',
                                   verbose_name='客户端', db_comment='发起调用的客户端标识(User-Agent)')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='success',
                              verbose_name='调用结果', db_comment='调用结果')
    duration_ms = models.IntegerField(default=0, verbose_name='耗时(毫秒)', db_comment='耗时(毫秒)')
    error = models.TextField(blank=True, default='', verbose_name='错误信息', db_comment='错误信息(截断)')
    created_at = models.DateTimeField(default=timezone.now, verbose_name='调用时间', db_comment='调用时间')

    class Meta:
        db_table_comment = 'MCP工具调用日志'
        db_table = 'mcp_call_logs'
        verbose_name = 'MCP调用日志'
        verbose_name_plural = 'MCP调用日志'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at'], name='mcp_log_created_idx'),
            models.Index(fields=['tool_name'], name='mcp_log_tool_idx'),
        ]

    def __str__(self):
        return f'{self.tool_name} @ {self.created_at}'


class McpPendingConfirm(models.Model):
    """危险操作待确认记录（preview→confirm 两段式）。

    preview 时创建（status=pending），confirm 消费后置为 consumed；
    监控页可手动批准（approved，服务端触发执行）或拒绝（rejected，令牌作废）；
    超时未消费视为 expired。
    """

    STATUS_CHOICES = [
        ('pending', '待确认'),
        ('approved', '已批准'),
        ('rejected', '已拒绝'),
        ('consumed', '已消费'),
        ('expired', '已过期'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='mcp_pending_confirms', verbose_name='发起用户', db_comment='发起用户')
    tool_name = models.CharField(max_length=100, verbose_name='工具名称', db_comment='待确认的工具名称')
    arguments = models.JSONField(default=dict, blank=True, verbose_name='工具参数', db_comment='工具参数(JSON)')
    preview = models.TextField(blank=True, default='', verbose_name='影响预览', db_comment='影响描述文本')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending',
                              verbose_name='状态', db_comment='状态')
    awaiting_human = models.BooleanField(default=False, verbose_name='等待人工审批',
                                         db_comment='人工审批模式下 confirm 已转待批')
    result = models.JSONField(default=dict, blank=True, verbose_name='执行结果', db_comment='批准/确认后执行结果摘要')
    expires_at = models.DateTimeField(verbose_name='过期时间', db_comment='confirm令牌过期时间(+5分钟)')
    created_at = models.DateTimeField(default=timezone.now, verbose_name='创建时间', db_comment='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间', db_comment='更新时间')

    class Meta:
        db_table_comment = 'MCP危险操作待确认'
        db_table = 'mcp_pending_confirms'
        verbose_name = 'MCP待确认操作'
        verbose_name_plural = 'MCP待确认操作'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at'], name='mcp_pending_status_idx'),
        ]

    def __str__(self):
        return f'{self.tool_name}({self.status})'

    @property
    def is_expired(self):
        return self.expires_at < timezone.now()

    @staticmethod
    def default_expires_at():
        return timezone.now() + timedelta(seconds=CONFIRM_TOKEN_TTL_SECONDS)
