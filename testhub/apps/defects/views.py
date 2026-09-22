import csv
import os
from datetime import timedelta

from django.db import models, transaction
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from rest_framework import filters, parsers, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from apps.projects.models import Project, ProjectMember
from apps.users.models import User
from apps.versions.models import Version
from .models import Defect, DefectAttachment, DefectComment, DefectTransition
from .serializers import (
    DefectActionSerializer,
    DefectAttachmentSerializer,
    DefectBulkActionSerializer,
    DefectCommentSerializer,
    DefectDetailSerializer,
    DefectListSerializer,
    DefectWriteSerializer,
)


class DefectPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


class DefectViewSet(viewsets.ModelViewSet):
    attachment_max_size = 10 * 1024 * 1024
    allowed_attachment_extensions = {
        '.txt',
        '.log',
        '.pdf',
        '.png',
        '.jpg',
        '.jpeg',
        '.gif',
        '.webp',
        '.doc',
        '.docx',
        '.xls',
        '.xlsx',
        '.zip',
    }
    allowed_attachment_content_types = {
        'text/plain',
        'application/pdf',
        'image/png',
        'image/jpeg',
        'image/gif',
        'image/webp',
        'application/msword',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.ms-excel',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/zip',
        'application/x-zip-compressed',
    }
    queryset = Defect.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = DefectPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['code', 'title', 'description', 'module']
    ordering_fields = [
        'created_at',
        'updated_at',
        'priority',
        'severity',
        'status',
        'resolved_at',
        'closed_at',
        'due_at',
    ]
    ordering = ['-created_at']
    # 缺陷状态流转规则：每个状态可流转到的目标状态集合。
    # 幂等流转（from == to，如对“修复中”缺陷再次“开始修复”、对已指派缺陷重新指派）单独判定为合法。
    TRANSITION_MAP = {
        'new': {'assigned', 'rejected'},
        'assigned': {'in_progress', 'resolved', 'rejected'},
        'in_progress': {'assigned', 'resolved', 'rejected'},
        'resolved': {'verified', 'reopened'},
        'verified': {'closed', 'reopened'},
        'rejected': {'reopened', 'closed'},
        'reopened': {'assigned', 'in_progress', 'resolved', 'rejected'},
        'closed': {'reopened'},
    }
    # 目标状态 -> 用户操作名称（用于友好提示）
    STATUS_ACTION_LABELS = {
        'assigned': '指派',
        'in_progress': '开始修复',
        'resolved': '提交修复',
        'verified': '回归通过',
        'rejected': '驳回',
        'reopened': '重新打开',
        'closed': '关闭',
    }
    # 非项目成员凭"自我角色"执行流转/协作动作的范围。
    # assignee=当前处理人, reporter=提交人；verify 仅放行 reporter，避免处理人自验自己的修复。
    ACTION_SELF_ROLES = {
        'assign': {'assignee', 'reporter'},
        'start_progress': {'assignee'},
        'resolve': {'assignee'},
        'reject': {'assignee'},
        'verify': {'reporter'},
        'reopen': {'assignee', 'reporter'},
        'close': {'assignee', 'reporter'},
        'add_comment': {'assignee', 'reporter'},
        'upload_attachment': {'assignee', 'reporter'},
    }

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return DefectWriteSerializer
        if self.action == 'retrieve':
            return DefectDetailSerializer
        return DefectListSerializer

    def get_queryset(self):
        user = self.request.user
        accessible_projects = self.get_user_accessible_projects(user)
        # 可见范围：我能访问的项目内的缺陷 + 我个人参与的缺陷
        # （作为提交人/处理人/验证人/修复人）。这样被指派给我但不在我项目里的缺陷也能看到与处理。
        queryset = Defect.objects.filter(
            models.Q(project__in=accessible_projects)
            | models.Q(reporter=user)
            | models.Q(assignee=user)
            | models.Q(verifier=user)
            | models.Q(resolver=user)
        ).select_related(
            'project',
            'version',
            'reporter',
            'assignee',
            'verifier',
            'resolver',
            'related_testcase',
        ).prefetch_related(
            'transitions',
            'transitions__operator',
            'transitions__target_user',
            'comments',
            'attachments',
        ).distinct()
        return self.apply_query_filters(queryset)

    def perform_create(self, serializer):
        defect = serializer.save(reporter=self.request.user)
        DefectTransition.objects.create(
            defect=defect,
            from_status='',
            to_status=defect.status,
            operator=self.request.user,
            target_user=defect.assignee,
            comment='创建缺陷',
        )

    def get_user_accessible_projects(self, user):
        return Project.objects.filter(
            models.Q(owner=user) | models.Q(members=user)
        ).distinct()

    def apply_query_filters(self, queryset):
        params = self.request.query_params
        exact_filters = {
            'project': 'project_id',
            'version': 'version_id',
            'status': 'status',
            'severity': 'severity',
            'priority': 'priority',
            'reporter': 'reporter_id',
            'assignee': 'assignee_id',
            'verifier': 'verifier_id',
        }
        for param_name, field_name in exact_filters.items():
            value = params.get(param_name)
            if value not in (None, ''):
                queryset = queryset.filter(**{field_name: value})

        module = params.get('module')
        if module:
            queryset = queryset.filter(module__icontains=module)

        created_after = params.get('created_after')
        if created_after:
            queryset = queryset.filter(created_at__date__gte=created_after)

        created_before = params.get('created_before')
        if created_before:
            queryset = queryset.filter(created_at__date__lte=created_before)

        return queryset

    def ensure_project_access(self, project):
        if not self.get_user_accessible_projects(self.request.user).filter(id=project.id).exists():
            return Response({'error': '无权访问该项目'}, status=status.HTTP_403_FORBIDDEN)
        return None

    def get_project_role(self, project, user):
        if project.owner_id == user.id:
            return 'owner'
        member = ProjectMember.objects.filter(project=project, user=user).first()
        return member.role if member else None

    def ensure_project_role(self, project, allowed_roles):
        role = self.get_project_role(project, self.request.user)
        if role not in allowed_roles:
            return Response({'error': '无权执行该操作'}, status=status.HTTP_403_FORBIDDEN)
        return None

    def ensure_defect_action_permission(self, defect, allowed_roles, self_roles=None):
        """流转/协作动作权限：项目成员按角色放行；否则若用户是该缺陷的处理人/提交人且该动作允许自我角色，也放行。"""
        user = self.request.user
        if self.get_project_role(defect.project, user) in allowed_roles:
            return None
        if self_roles:
            if 'assignee' in self_roles and defect.assignee_id == user.id:
                return None
            if 'reporter' in self_roles and defect.reporter_id == user.id:
                return None
        return Response(
            {'error': '无权执行该操作：需为项目成员，或该缺陷的处理人/提交人'},
            status=status.HTTP_403_FORBIDDEN,
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        access_error = self.ensure_project_access(serializer.validated_data['project'])
        if access_error:
            return access_error
        role_error = self.ensure_project_role(
            serializer.validated_data['project'],
            {'owner', 'admin', 'developer', 'tester'},
        )
        if role_error:
            return role_error
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(DefectDetailSerializer(serializer.instance).data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        access_error = self.ensure_project_access(serializer.validated_data.get('project', instance.project))
        if access_error:
            return access_error
        role_error = self.ensure_project_role(instance.project, {'owner', 'admin', 'developer', 'tester'})
        if role_error:
            return role_error
        serializer.save()
        return Response(DefectDetailSerializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        role_error = self.ensure_project_role(instance.project, {'owner', 'admin'})
        if role_error:
            return role_error
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        defect = self.get_object()
        role_error = self.ensure_defect_action_permission(defect, {'owner', 'admin', 'tester'}, self_roles=self.ACTION_SELF_ROLES['assign'])
        if role_error:
            return role_error
        serializer = DefectActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assignee = self.get_user(serializer.validated_data.get('assignee_id'), required=True)
        if isinstance(assignee, Response):
            return assignee
        return self.transition_defect(
            defect,
            'assigned',
            serializer.validated_data.get('comment', ''),
            assignee=assignee,
        )

    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        defect = self.get_object()
        role_error = self.ensure_defect_action_permission(defect, {'owner', 'admin', 'developer'}, self_roles=self.ACTION_SELF_ROLES['resolve'])
        if role_error:
            return role_error
        serializer = DefectActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self.transition_defect(
            defect,
            'resolved',
            serializer.validated_data.get('comment', ''),
            resolver=request.user,
            resolved_at=timezone.now(),
        )

    @action(detail=True, methods=['post'], url_path='start-progress')
    def start_progress(self, request, pk=None):
        defect = self.get_object()
        role_error = self.ensure_defect_action_permission(defect, {'owner', 'admin', 'developer'}, self_roles=self.ACTION_SELF_ROLES['start_progress'])
        if role_error:
            return role_error
        serializer = DefectActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self.transition_defect(
            defect,
            'in_progress',
            serializer.validated_data.get('comment', ''),
            resolver=request.user,
        )

    @action(detail=True, methods=['post'])
    def verify(self, request, pk=None):
        defect = self.get_object()
        role_error = self.ensure_defect_action_permission(defect, {'owner', 'admin', 'tester'}, self_roles=self.ACTION_SELF_ROLES['verify'])
        if role_error:
            return role_error
        serializer = DefectActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self.transition_defect(
            defect,
            'verified',
            serializer.validated_data.get('comment', ''),
            verifier=request.user,
        )

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        defect = self.get_object()
        role_error = self.ensure_defect_action_permission(defect, {'owner', 'admin', 'developer'}, self_roles=self.ACTION_SELF_ROLES['reject'])
        if role_error:
            return role_error
        serializer = DefectActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self.transition_defect(
            defect,
            'rejected',
            serializer.validated_data.get('comment', ''),
            resolver=request.user,
        )

    @action(detail=True, methods=['post'])
    def reopen(self, request, pk=None):
        defect = self.get_object()
        role_error = self.ensure_defect_action_permission(defect, {'owner', 'admin', 'tester', 'developer'}, self_roles=self.ACTION_SELF_ROLES['reopen'])
        if role_error:
            return role_error
        serializer = DefectActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self.transition_defect(
            defect,
            'reopened',
            serializer.validated_data.get('comment', ''),
            resolved_at=None,
            closed_at=None,
        )

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        defect = self.get_object()
        role_error = self.ensure_defect_action_permission(defect, {'owner', 'admin', 'tester'}, self_roles=self.ACTION_SELF_ROLES['close'])
        if role_error:
            return role_error
        serializer = DefectActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self.transition_defect(
            defect,
            'closed',
            serializer.validated_data.get('comment', ''),
            closed_at=timezone.now(),
        )

    @action(detail=True, methods=['post'], url_path='comments')
    def add_comment(self, request, pk=None):
        defect = self.get_object()
        role_error = self.ensure_defect_action_permission(defect, {'owner', 'admin', 'developer', 'tester'}, self_roles=self.ACTION_SELF_ROLES['add_comment'])
        if role_error:
            return role_error
        serializer = DefectCommentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = serializer.save(defect=defect, author=request.user)
        return Response(DefectCommentSerializer(comment).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=['post'],
        url_path='attachments',
        parser_classes=[parsers.MultiPartParser, parsers.FormParser],
    )
    def upload_attachment(self, request, pk=None):
        defect = self.get_object()
        role_error = self.ensure_defect_action_permission(defect, {'owner', 'admin', 'developer', 'tester'}, self_roles=self.ACTION_SELF_ROLES['upload_attachment'])
        if role_error:
            return role_error
        upload_file = request.FILES.get('file')
        if not upload_file:
            return Response({'file': '附件文件不能为空'}, status=status.HTTP_400_BAD_REQUEST)
        validation_error = self.validate_attachment(upload_file)
        if validation_error:
            return validation_error
        attachment = DefectAttachment.objects.create(
            defect=defect,
            name=request.data.get('name') or upload_file.name,
            file=upload_file,
            uploaded_by=request.user,
        )
        return Response(DefectAttachmentSerializer(attachment).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='bulk-action')
    def bulk_action(self, request):
        serializer = DefectBulkActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        defects = list(self.get_queryset().filter(id__in=data['defect_ids']))
        defect_map = {defect.id: defect for defect in defects}
        results = []

        with transaction.atomic():
            for defect_id in data['defect_ids']:
                defect = defect_map.get(defect_id)
                if not defect:
                    results.append({'id': defect_id, 'success': False, 'error': '缺陷不存在或无权访问'})
                    continue
                result = self.apply_bulk_action(defect, data)
                results.append({'id': defect_id, **result})

        return Response({'results': results})

    @action(detail=False, methods=['get'])
    def summary(self, request):
        queryset = self.get_queryset()
        total = queryset.count()
        closed_count = queryset.filter(status='closed').count()
        resolved_count = queryset.filter(status='resolved').count()
        reopened_count = queryset.filter(status='reopened').count()
        severe_count = queryset.filter(severity__in=['blocker', 'critical']).count()
        overdue_count = queryset.exclude(status='closed').filter(due_at__lt=timezone.now()).count()

        return Response({
            'total': total,
            'closed': closed_count,
            'resolved': resolved_count,
            'reopened': reopened_count,
            'severe': severe_count,
            'overdue': overdue_count,
            'close_rate': round(closed_count / total * 100, 1) if total else 0,
            'status_distribution': self.count_by(queryset, 'status'),
            'severity_distribution': self.count_by(queryset, 'severity'),
            'priority_distribution': self.count_by(queryset, 'priority'),
            'version_distribution': self.count_by(queryset, 'version__name'),
            'module_distribution': self.count_by(queryset, 'module'),
            'assignee_distribution': self.count_by(queryset, 'assignee__username'),
        })

    @action(detail=False, methods=['get'])
    def trend(self, request):
        queryset = self.get_queryset()
        days = int(request.query_params.get('days', 7))
        start_date = timezone.localdate() - timedelta(days=days - 1)
        created_data = self.count_by_date(queryset.filter(created_at__date__gte=start_date), 'created_at')
        closed_data = self.count_by_date(queryset.filter(closed_at__date__gte=start_date), 'closed_at')
        result = []
        for index in range(days):
            day = start_date + timedelta(days=index)
            key = day.strftime('%Y-%m-%d')
            result.append({
                'date': key,
                'created': created_data.get(key, 0),
                'closed': closed_data.get(key, 0),
            })
        return Response(result)

    @action(detail=False, methods=['get'])
    def export(self, request):
        queryset = self.get_queryset().order_by('-created_at')
        if request.query_params.get('format') == 'csv':
            return self.export_csv(queryset)
        return self.export_excel(queryset)

    @action(detail=False, methods=['get'], url_path='export-report')
    def export_report(self, request):
        queryset = self.get_queryset()
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="defect-report.pdf"'
        pdf = canvas.Canvas(response, pagesize=A4)
        font_name = self.register_pdf_font()
        width, height = A4
        y = height - 50
        pdf.setFont(font_name, 16)
        pdf.drawString(50, y, 'TestHub 缺陷测试报告')
        y -= 36
        pdf.setFont(font_name, 11)
        summary_lines = [
            f'缺陷总数: {queryset.count()}',
            f'新建缺陷: {queryset.filter(status="new").count()}',
            f'待回归缺陷: {queryset.filter(status="resolved").count()}',
            f'已关闭缺陷: {queryset.filter(status="closed").count()}',
            f'高风险缺陷: {queryset.filter(severity__in=["blocker", "critical"]).count()}',
        ]
        for line in summary_lines:
            pdf.drawString(50, y, line)
            y -= 22
        y -= 12
        pdf.setFont(font_name, 12)
        pdf.drawString(50, y, '高风险缺陷明细')
        y -= 24
        pdf.setFont(font_name, 10)
        for defect in queryset.filter(severity__in=['blocker', 'critical']).order_by('-created_at')[:20]:
            text = f'{defect.code} | {defect.title[:40]} | {defect.get_status_display()} | {defect.get_priority_display()}'
            pdf.drawString(50, y, text)
            y -= 18
            if y < 60:
                pdf.showPage()
                y = height - 50
                pdf.setFont(font_name, 10)
        pdf.save()
        return response

    def transition_defect(self, defect, to_status, comment='', **updates):
        from_status = defect.status
        if not self.is_transition_allowed(from_status, to_status):
            return Response(
                {'error': self.get_transition_error(from_status, to_status)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        for field_name, value in updates.items():
            setattr(defect, field_name, value)
        defect.status = to_status
        defect.save()
        DefectTransition.objects.create(
            defect=defect,
            from_status=from_status,
            to_status=to_status,
            operator=self.request.user,
            target_user=updates.get('assignee'),
            comment=comment,
        )
        return Response(DefectDetailSerializer(defect).data)

    def is_transition_allowed(self, from_status, to_status):
        # 幂等流转（重复点击同一动作、对已指派缺陷重新指派等）视为合法
        if from_status == to_status:
            return True
        return to_status in self.TRANSITION_MAP.get(from_status, set())

    def get_transition_error(self, from_status, to_status):
        """生成友好的状态流转失败提示：点明当前状态与所尝试操作，并列出可执行操作。"""
        status_labels = dict(Defect.STATUS_CHOICES)
        from_label = status_labels.get(from_status, from_status)
        attempted = self.STATUS_ACTION_LABELS.get(to_status) or status_labels.get(to_status, to_status)
        allowed = self.TRANSITION_MAP.get(from_status, set())
        # 按 STATUS_CHOICES 既定顺序列出可执行操作，排除自身态与无对应按钮的目标态
        allowed_actions = [
            self.STATUS_ACTION_LABELS[s]
            for s in status_labels
            if s in allowed and s != from_status and s in self.STATUS_ACTION_LABELS
        ]
        hint = f'当前状态「{from_label}」无法执行「{attempted}」操作'
        if allowed_actions:
            hint += f'，可执行：{"、".join(allowed_actions)}'
        return hint

    def get_user(self, user_id, required=False):
        if not user_id:
            if required:
                return Response({'error': '用户ID不能为空'}, status=status.HTTP_400_BAD_REQUEST)
            return None
        try:
            return User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({'error': '用户不存在'}, status=status.HTTP_404_NOT_FOUND)

    def apply_bulk_action(self, defect, data):
        action_name = data['action']
        comment = data.get('comment', '')
        if action_name == 'assign':
            assignee = self.get_user(data.get('assignee_id'), required=True)
            if isinstance(assignee, Response):
                return {'success': False, 'error': assignee.data['error']}
            response = self.transition_defect(defect, 'assigned', comment, assignee=assignee)
        elif action_name == 'set_priority':
            defect.priority = data['priority']
            defect.save(update_fields=['priority', 'updated_at'])
            return {'success': True, 'status': defect.status}
        elif action_name == 'set_version':
            version = self.get_version_for_defect(defect, data.get('version_id'))
            if isinstance(version, Response):
                return {'success': False, 'error': version.data['error']}
            defect.version = version
            defect.save(update_fields=['version', 'updated_at'])
            return {'success': True, 'status': defect.status}
        elif action_name == 'close':
            response = self.transition_defect(defect, 'closed', comment, closed_at=timezone.now())
        elif action_name == 'reopen':
            response = self.transition_defect(defect, 'reopened', comment, resolved_at=None, closed_at=None)
        else:
            return {'success': False, 'error': '不支持的批量动作'}

        if response.status_code >= 400:
            return {'success': False, 'error': response.data.get('error', '处理失败')}
        return {'success': True, 'status': response.data['status']}

    def get_version_for_defect(self, defect, version_id):
        try:
            version = Version.objects.get(id=version_id)
        except Version.DoesNotExist:
            return Response({'error': '版本不存在'}, status=status.HTTP_404_NOT_FOUND)
        if not version.projects.filter(id=defect.project_id).exists():
            return Response({'error': '版本不属于缺陷项目'}, status=status.HTTP_400_BAD_REQUEST)
        return version

    def count_by(self, queryset, field_name):
        return list(
            queryset.values(field_name).annotate(count=Count('id')).order_by(field_name)
        )

    def count_by_date(self, queryset, field_name):
        data = queryset.annotate(day=TruncDate(field_name)).values('day').annotate(count=Count('id'))
        return {
            item['day'].strftime('%Y-%m-%d'): item['count']
            for item in data
            if item['day']
        }

    def register_pdf_font(self):
        font_name = 'DefectPdfFont'
        if font_name in pdfmetrics.getRegisteredFontNames():
            return font_name

        font_paths = [
            'C:/Windows/Fonts/simhei.ttf',
            'C:/Windows/Fonts/msyh.ttf',
            'C:/Windows/Fonts/STSONG.TTF',
            '/System/Library/Fonts/PingFang.ttc',
            '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        ]
        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    pdfmetrics.registerFont(TTFont(font_name, font_path))
                    return font_name
                except Exception:
                    continue
        return 'Helvetica'

    def validate_attachment(self, upload_file):
        ext = os.path.splitext(upload_file.name)[1].lower()
        if ext not in self.allowed_attachment_extensions:
            allowed = '、'.join(sorted(self.allowed_attachment_extensions))
            return Response(
                {'file': f'不支持的附件类型，请上传以下格式之一：{allowed}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if upload_file.size > self.attachment_max_size:
            return Response(
                {'file': '附件大小不能超过 10MB'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        content_type = (upload_file.content_type or '').lower()
        if content_type and content_type not in self.allowed_attachment_content_types:
            return Response(
                {'file': f'不支持的附件内容类型：{content_type}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return None

    def export_excel(self, queryset):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Defects'
        headers = [
            '缺陷编号',
            '标题',
            '项目',
            '版本',
            '模块',
            '严重级别',
            '优先级',
            '状态',
            '提交人',
            '处理人',
            '验证人',
            '创建时间',
            '解决时间',
            '关闭时间',
        ]
        sheet.append(headers)
        for defect in queryset:
            sheet.append([
                defect.code,
                defect.title,
                defect.project.name if defect.project else '',
                defect.version.name if defect.version else '',
                defect.module,
                defect.severity,
                defect.priority,
                defect.status,
                defect.reporter.username if defect.reporter else '',
                defect.assignee.username if defect.assignee else '',
                defect.verifier.username if defect.verifier else '',
                timezone.localtime(defect.created_at).strftime('%Y-%m-%d %H:%M:%S') if defect.created_at else '',
                timezone.localtime(defect.resolved_at).strftime('%Y-%m-%d %H:%M:%S') if defect.resolved_at else '',
                timezone.localtime(defect.closed_at).strftime('%Y-%m-%d %H:%M:%S') if defect.closed_at else '',
            ])
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="defects.xlsx"'
        workbook.save(response)
        return response

    def export_csv(self, queryset):
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="defects.csv"'
        response.write('\ufeff')
        writer = csv.writer(response)
        writer.writerow(['缺陷编号', '标题', '项目', '版本', '模块', '严重级别', '优先级', '状态'])
        for defect in queryset:
            writer.writerow([
                defect.code,
                defect.title,
                defect.project.name if defect.project else '',
                defect.version.name if defect.version else '',
                defect.module,
                defect.severity,
                defect.priority,
                defect.status,
            ])
        return response
