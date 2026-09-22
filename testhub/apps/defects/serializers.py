from rest_framework import serializers

from apps.projects.models import Project
from apps.testcases.models import TestCase
from apps.users.models import User
from apps.users.serializers import UserSimpleSerializer
from apps.versions.models import Version
from .models import Defect, DefectAttachment, DefectComment, DefectTransition


class ProjectSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ('id', 'name')


class VersionSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Version
        fields = ('id', 'name', 'is_baseline')


class TestCaseSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestCase
        fields = ('id', 'title')


class DefectTransitionSerializer(serializers.ModelSerializer):
    operator = UserSimpleSerializer(read_only=True)
    target_user = UserSimpleSerializer(read_only=True)

    class Meta:
        model = DefectTransition
        fields = ('id', 'from_status', 'to_status', 'operator', 'target_user', 'comment', 'created_at')
        read_only_fields = fields


class DefectCommentSerializer(serializers.ModelSerializer):
    author = UserSimpleSerializer(read_only=True)

    class Meta:
        model = DefectComment
        fields = ('id', 'author', 'content', 'created_at')
        read_only_fields = ('id', 'author', 'created_at')


class DefectAttachmentSerializer(serializers.ModelSerializer):
    uploaded_by = UserSimpleSerializer(read_only=True)

    class Meta:
        model = DefectAttachment
        fields = ('id', 'name', 'file', 'uploaded_by', 'uploaded_at')
        read_only_fields = ('id', 'uploaded_by', 'uploaded_at')


class DefectListSerializer(serializers.ModelSerializer):
    project = ProjectSimpleSerializer(read_only=True)
    version = VersionSimpleSerializer(read_only=True)
    reporter = UserSimpleSerializer(read_only=True)
    assignee = UserSimpleSerializer(read_only=True)
    verifier = UserSimpleSerializer(read_only=True)
    resolver = UserSimpleSerializer(read_only=True)
    related_testcase = TestCaseSimpleSerializer(read_only=True)

    class Meta:
        model = Defect
        fields = (
            'id',
            'code',
            'title',
            'project',
            'version',
            'module',
            'severity',
            'priority',
            'status',
            'defect_type',
            'source',
            'reporter',
            'assignee',
            'verifier',
            'resolver',
            'related_testcase',
            'due_at',
            'resolved_at',
            'closed_at',
            'created_at',
            'updated_at',
        )
        read_only_fields = fields


class DefectDetailSerializer(DefectListSerializer):
    description = serializers.CharField(read_only=True)
    reproduce_steps = serializers.CharField(read_only=True)
    expected_result = serializers.CharField(read_only=True)
    actual_result = serializers.CharField(read_only=True)
    transitions = DefectTransitionSerializer(many=True, read_only=True)
    comments = DefectCommentSerializer(many=True, read_only=True)
    attachments = DefectAttachmentSerializer(many=True, read_only=True)

    class Meta(DefectListSerializer.Meta):
        fields = DefectListSerializer.Meta.fields + (
            'description',
            'reproduce_steps',
            'expected_result',
            'actual_result',
            'transitions',
            'comments',
            'attachments',
        )
        read_only_fields = fields


class DefectWriteSerializer(serializers.ModelSerializer):
    project_id = serializers.IntegerField(required=False, write_only=True)
    version_id = serializers.IntegerField(required=False, allow_null=True, write_only=True)
    assignee_id = serializers.IntegerField(required=False, allow_null=True, write_only=True)
    verifier_id = serializers.IntegerField(required=False, allow_null=True, write_only=True)
    related_testcase_id = serializers.IntegerField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = Defect
        fields = (
            'title',
            'description',
            'reproduce_steps',
            'expected_result',
            'actual_result',
            'project_id',
            'version_id',
            'module',
            'severity',
            'priority',
            'defect_type',
            'source',
            'assignee_id',
            'verifier_id',
            'related_testcase_id',
            'due_at',
        )

    def validate(self, attrs):
        project_id = attrs.get('project_id')
        if project_id in (None, '') and self.instance is None:
            raise serializers.ValidationError({'project_id': '项目不能为空'})
        project = self._get_project(project_id) if project_id not in (None, '') else self.instance.project
        version = self._get_related_value(attrs, 'version_id', Version, 'version')
        related_testcase = self._get_related_value(attrs, 'related_testcase_id', TestCase, 'related_testcase')

        if version and not version.projects.filter(id=project.id).exists():
            raise serializers.ValidationError({'version_id': '版本不属于所选项目'})

        if related_testcase and related_testcase.project_id != project.id:
            raise serializers.ValidationError({'related_testcase_id': '测试用例不属于所选项目'})

        attrs['project'] = project
        attrs['version'] = version
        attrs['assignee'] = self._get_related_value(attrs, 'assignee_id', User, 'assignee')
        attrs['verifier'] = self._get_related_value(attrs, 'verifier_id', User, 'verifier')
        attrs['related_testcase'] = related_testcase
        return attrs

    def create(self, validated_data):
        self._drop_write_only_fields(validated_data)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        self._drop_write_only_fields(validated_data)
        return super().update(instance, validated_data)

    def _get_project(self, project_id):
        try:
            return Project.objects.get(id=project_id)
        except Project.DoesNotExist as exc:
            raise serializers.ValidationError({'project_id': '项目不存在'}) from exc

    def _get_optional_object(self, model, object_id, field_name):
        if object_id in (None, ''):
            return None
        try:
            return model.objects.get(id=object_id)
        except model.DoesNotExist as exc:
            raise serializers.ValidationError({field_name: '对象不存在'}) from exc

    def _get_related_value(self, attrs, write_field, model, model_field):
        if write_field in attrs:
            return self._get_optional_object(model, attrs.get(write_field), write_field)
        if self.instance is not None:
            return getattr(self.instance, model_field)
        return None

    def _drop_write_only_fields(self, validated_data):
        for field_name in (
            'project_id',
            'version_id',
            'assignee_id',
            'verifier_id',
            'related_testcase_id',
        ):
            validated_data.pop(field_name, None)


class DefectActionSerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True)
    assignee_id = serializers.IntegerField(required=False, allow_null=True)
    verifier_id = serializers.IntegerField(required=False, allow_null=True)
    priority = serializers.ChoiceField(choices=Defect.PRIORITY_CHOICES, required=False)
    version_id = serializers.IntegerField(required=False, allow_null=True)


class DefectBulkActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=('assign', 'set_priority', 'set_version', 'close', 'reopen'))
    defect_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    assignee_id = serializers.IntegerField(required=False, allow_null=True)
    priority = serializers.ChoiceField(choices=Defect.PRIORITY_CHOICES, required=False)
    version_id = serializers.IntegerField(required=False, allow_null=True)
    comment = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        action = attrs['action']
        required_map = {
            'assign': 'assignee_id',
            'set_priority': 'priority',
            'set_version': 'version_id',
        }
        required_field = required_map.get(action)
        if required_field and attrs.get(required_field) in (None, ''):
            raise serializers.ValidationError({required_field: '该字段为必填项'})
        return attrs
