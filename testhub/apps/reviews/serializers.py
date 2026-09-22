from rest_framework import serializers
from .models import TestCaseReview, ReviewAssignment, TestCaseReviewComment, ReviewTemplate


# 简化的序列化器，避免循环导入
class SimpleUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    email = serializers.EmailField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class SimpleProjectSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField()


class SimpleTestCaseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    test_type = serializers.CharField()
    priority = serializers.CharField()
    status = serializers.CharField()
    author = SimpleUserSerializer()


class ReviewAssignmentSerializer(serializers.ModelSerializer):
    reviewer = SimpleUserSerializer(read_only=True)
    
    class Meta:
        model = ReviewAssignment
        fields = ['id', 'reviewer', 'status', 'comment', 'checklist_results', 'reviewed_at', 'assigned_at']


class TestCaseReviewCommentSerializer(serializers.ModelSerializer):
    author = SimpleUserSerializer(read_only=True)
    testcase = SimpleTestCaseSerializer(read_only=True)
    
    class Meta:
        model = TestCaseReviewComment
        fields = ['id', 'testcase', 'author', 'comment_type', 'content', 'step_number', 
                 'is_resolved', 'created_at', 'updated_at']


class TestCaseReviewCommentCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestCaseReviewComment
        fields = ['review', 'testcase', 'comment_type', 'content', 'step_number']
        extra_kwargs = {
            'testcase': {'required': False, 'allow_null': True}
        }


class ReviewTemplateSerializer(serializers.ModelSerializer):
    creator = SimpleUserSerializer(read_only=True)
    project = SimpleProjectSerializer(many=True, read_only=True)
    default_reviewers = SimpleUserSerializer(many=True, read_only=True)
    
    class Meta:
        model = ReviewTemplate
        fields = ['id', 'name', 'description', 'project', 'creator', 'checklist',
                 'default_reviewers', 'is_active', 'created_at', 'updated_at']


class TestCaseReviewSerializer(serializers.ModelSerializer):
    creator = SimpleUserSerializer(read_only=True)
    projects = SimpleProjectSerializer(many=True, read_only=True)
    testcases = SimpleTestCaseSerializer(many=True, read_only=True)
    template = ReviewTemplateSerializer(read_only=True)
    assignments = ReviewAssignmentSerializer(source='reviewassignment_set', many=True, read_only=True)
    comments = TestCaseReviewCommentSerializer(many=True, read_only=True)
    
    class Meta:
        model = TestCaseReview
        fields = ['id', 'title', 'description', 'projects', 'testcases', 'creator', 
                 'template', 'status', 'priority', 'deadline', 'created_at', 'updated_at', 
                 'completed_at', 'assignments', 'comments']


# 创建和更新用的简化序列化器
class TestCaseReviewCreateSerializer(serializers.ModelSerializer):
    testcases = serializers.ListField(child=serializers.IntegerField(), write_only=True)
    reviewers = serializers.ListField(child=serializers.IntegerField(), write_only=True)
    projects = serializers.ListField(child=serializers.IntegerField(), write_only=True)
    template = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    
    class Meta:
        model = TestCaseReview
        fields = ['title', 'description', 'projects', 'priority', 'deadline', 'testcases', 'reviewers', 'template']
    
    def create(self, validated_data):
        testcases_ids = validated_data.pop('testcases', [])
        reviewers_ids = validated_data.pop('reviewers', [])
        projects_ids = validated_data.pop('projects', [])
        template_id = validated_data.pop('template', None)
        
        review = TestCaseReview.objects.create(**validated_data)
        
        # 添加项目关联
        if projects_ids:
            review.projects.set(projects_ids)
        
        # 添加模板关联
        if template_id:
            try:
                from .models import ReviewTemplate
                template = ReviewTemplate.objects.get(id=template_id)
                review.template = template
                review.save()
            except ReviewTemplate.DoesNotExist:
                pass
        
        # 添加测试用例
        if testcases_ids:
            review.testcases.set(testcases_ids)
        
        # 添加评审人员
        if reviewers_ids:
            from apps.users.models import User
            for reviewer_id in reviewers_ids:
                try:
                    reviewer = User.objects.get(id=reviewer_id)
                    ReviewAssignment.objects.create(review=review, reviewer=reviewer)
                except User.DoesNotExist:
                    continue
        
        return review

    def update(self, instance, validated_data):
        # 弹出关系字段（write_only 的 ListField / IntegerField），单独处理，
        # 否则默认 update 会把 template 整数 id 直接赋给 FK（触发 ValueError），
        # 也会把 testcases/projects/reviewers 当普通属性 set，无法正确更新 M2M。
        testcases_ids = validated_data.pop('testcases', None)
        reviewers_ids = validated_data.pop('reviewers', None)
        projects_ids = validated_data.pop('projects', None)
        template_id = validated_data.pop('template', None)

        # 普通字段交给默认逻辑（title/description/priority/deadline）
        instance = super().update(instance, validated_data)

        # 项目关联
        if projects_ids is not None:
            instance.projects.set(projects_ids)

        # 模板关联（id -> ReviewTemplate 实例）
        if template_id is not None:
            try:
                from .models import ReviewTemplate
                instance.template = ReviewTemplate.objects.get(id=template_id)
            except ReviewTemplate.DoesNotExist:
                instance.template = None
            instance.save()
        elif template_id is None and 'template' in self.initial_data:
            # 显式传 null/0 时清空模板
            instance.template = None
            instance.save()

        # 测试用例
        if testcases_ids is not None:
            instance.testcases.set(testcases_ids)

        # 评审人员（through=ReviewAssignment，需重建）
        if reviewers_ids is not None:
            instance.reviewassignment_set.all().delete()
            from apps.users.models import User
            for reviewer_id in reviewers_ids:
                try:
                    reviewer = User.objects.get(id=reviewer_id)
                    ReviewAssignment.objects.create(review=instance, reviewer=reviewer)
                except User.DoesNotExist:
                    continue

        return instance


class ReviewTemplateCreateSerializer(serializers.ModelSerializer):
    default_reviewers = serializers.ListField(child=serializers.IntegerField(), write_only=True, required=False)
    project = serializers.ListField(child=serializers.IntegerField(), write_only=True)
    
    class Meta:
        model = ReviewTemplate
        fields = ['name', 'description', 'project', 'checklist', 'default_reviewers']
    
    def create(self, validated_data):
        default_reviewers_ids = validated_data.pop('default_reviewers', [])
        project_ids = validated_data.pop('project', [])
        template = ReviewTemplate.objects.create(**validated_data)
        
        # 添加项目关联
        if project_ids:
            template.project.set(project_ids)
        
        if default_reviewers_ids:
            template.default_reviewers.set(default_reviewers_ids)
        
        return template