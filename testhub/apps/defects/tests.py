from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.projects.models import Project, ProjectMember
from apps.users.models import User
from apps.versions.models import Version
from .models import Defect, DefectTransition


class DefectApiTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.tester = User.objects.create_user(username='tester', password='pass123456')
        self.developer = User.objects.create_user(username='developer', password='pass123456')
        self.viewer = User.objects.create_user(username='viewer', password='pass123456')
        self.other_user = User.objects.create_user(username='other', password='pass123456')
        self.project = Project.objects.create(name='TestHub', owner=self.tester)
        ProjectMember.objects.create(project=self.project, user=self.developer, role='developer')
        ProjectMember.objects.create(project=self.project, user=self.viewer, role='viewer')
        self.version = Version.objects.create(name='v1.0', created_by=self.tester)
        self.version.projects.add(self.project)
        self.client.force_authenticate(self.tester)
        self.list_url = reverse('defects-list')

    def test_create_defect(self):
        response = self.client.post(self.list_url, self.build_payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['title'], '登录失败')
        self.assertTrue(response.data['code'].startswith('BUG-'))
        self.assertEqual(response.data['status'], 'new')
        self.assertEqual(DefectTransition.objects.count(), 1)

    def test_filter_by_version_module_and_assignee(self):
        defect = Defect.objects.create(
            title='支付失败',
            project=self.project,
            version=self.version,
            module='支付',
            reporter=self.tester,
            assignee=self.developer,
        )
        Defect.objects.create(
            title='无权限数据',
            project=Project.objects.create(name='Other', owner=self.other_user),
            module='支付',
            reporter=self.other_user,
        )

        response = self.client.get(self.list_url, {
            'version': self.version.id,
            'module': '支付',
            'assignee': self.developer.id,
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result_ids = [item['id'] for item in response.data['results']]
        self.assertEqual(result_ids, [defect.id])

    def test_assign_and_close_defect(self):
        defect = Defect.objects.create(title='登录失败', project=self.project, reporter=self.tester)

        assign_url = reverse('defects-assign', args=[defect.id])
        assign_response = self.client.post(assign_url, {'assignee_id': self.developer.id}, format='json')

        self.assertEqual(assign_response.status_code, status.HTTP_200_OK)
        self.assertEqual(assign_response.data['status'], 'assigned')
        defect.refresh_from_db()
        self.assertEqual(defect.assignee_id, self.developer.id)

        defect.status = 'verified'
        defect.save(update_fields=['status'])
        close_url = reverse('defects-close', args=[defect.id])
        close_response = self.client.post(close_url, {'comment': '回归通过'}, format='json')

        self.assertEqual(close_response.status_code, status.HTTP_200_OK)
        self.assertEqual(close_response.data['status'], 'closed')
        defect.refresh_from_db()
        self.assertIsNotNone(defect.closed_at)

    def test_new_defect_cannot_close_directly(self):
        defect = Defect.objects.create(title='登录失败', project=self.project, reporter=self.tester)

        close_url = reverse('defects-close', args=[defect.id])
        close_response = self.client.post(close_url, {'comment': '直接关闭'}, format='json')

        self.assertEqual(close_response.status_code, status.HTTP_400_BAD_REQUEST)
        defect.refresh_from_db()
        self.assertEqual(defect.status, 'new')

    def test_start_progress_then_resolve_defect(self):
        defect = Defect.objects.create(
            title='提交按钮无响应',
            project=self.project,
            reporter=self.tester,
            assignee=self.developer,
            status='assigned',
        )

        self.client.force_authenticate(self.developer)
        start_url = reverse('defects-start-progress', args=[defect.id])
        start_response = self.client.post(start_url, {'comment': '开始定位问题'}, format='json')

        self.assertEqual(start_response.status_code, status.HTTP_200_OK)
        self.assertEqual(start_response.data['status'], 'in_progress')
        defect.refresh_from_db()
        self.assertEqual(defect.resolver_id, self.developer.id)

        resolve_url = reverse('defects-resolve', args=[defect.id])
        resolve_response = self.client.post(resolve_url, {'comment': '已修复'}, format='json')

        self.assertEqual(resolve_response.status_code, status.HTTP_200_OK)
        self.assertEqual(resolve_response.data['status'], 'resolved')
        defect.refresh_from_db()
        self.assertIsNotNone(defect.resolved_at)

    def test_add_comment_and_upload_attachment(self):
        defect = Defect.objects.create(title='登录失败', project=self.project, reporter=self.tester)

        comment_url = reverse('defects-add-comment', args=[defect.id])
        comment_response = self.client.post(comment_url, {'content': '请补充浏览器版本'}, format='json')

        self.assertEqual(comment_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(comment_response.data['content'], '请补充浏览器版本')

        attachment_url = reverse('defects-upload-attachment', args=[defect.id])
        upload_file = SimpleUploadedFile('evidence.txt', b'failed screenshot placeholder', content_type='text/plain')
        attachment_response = self.client.post(
            attachment_url,
            {'name': 'evidence.txt', 'file': upload_file},
            format='multipart',
        )

        self.assertEqual(attachment_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(attachment_response.data['name'], 'evidence.txt')

    def test_upload_attachment_rejects_invalid_extension_and_oversize(self):
        defect = Defect.objects.create(title='登录失败', project=self.project, reporter=self.tester)
        attachment_url = reverse('defects-upload-attachment', args=[defect.id])

        invalid_file = SimpleUploadedFile('script.exe', b'fake-binary', content_type='application/octet-stream')
        invalid_response = self.client.post(
            attachment_url,
            {'name': 'script.exe', 'file': invalid_file},
            format='multipart',
        )
        self.assertEqual(invalid_response.status_code, status.HTTP_400_BAD_REQUEST)

        large_file = SimpleUploadedFile(
            'huge.txt',
            b'a' * (10 * 1024 * 1024 + 1),
            content_type='text/plain',
        )
        large_response = self.client.post(
            attachment_url,
            {'name': 'huge.txt', 'file': large_file},
            format='multipart',
        )
        self.assertEqual(large_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_viewer_cannot_create_or_transition_defect(self):
        self.client.force_authenticate(self.viewer)
        create_response = self.client.post(self.list_url, self.build_payload(), format='json')

        self.assertEqual(create_response.status_code, status.HTTP_403_FORBIDDEN)

        defect = Defect.objects.create(title='登录失败', project=self.project, reporter=self.tester)
        assign_url = reverse('defects-assign', args=[defect.id])
        assign_response = self.client.post(assign_url, {'assignee_id': self.developer.id}, format='json')

        self.assertEqual(assign_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_summary(self):
        Defect.objects.create(title='阻塞缺陷', project=self.project, reporter=self.tester, severity='blocker')
        Defect.objects.create(title='已关闭缺陷', project=self.project, reporter=self.tester, status='closed')

        response = self.client.get(reverse('defects-summary'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total'], 2)
        self.assertEqual(response.data['closed'], 1)
        self.assertEqual(response.data['severe'], 1)

    def test_export_report(self):
        Defect.objects.create(title='阻塞缺陷', project=self.project, reporter=self.tester, severity='blocker')

        response = self.client.get(reverse('defects-export-report'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment; filename="defect-report.pdf"', response['Content-Disposition'])

    def build_payload(self):
        return {
            'title': '登录失败',
            'description': '输入正确账号密码后仍提示失败',
            'reproduce_steps': '1. 打开登录页\n2. 输入账号密码\n3. 点击登录',
            'expected_result': '登录成功',
            'actual_result': '提示登录失败',
            'project_id': self.project.id,
            'version_id': self.version.id,
            'module': '登录',
            'severity': 'critical',
            'priority': 'p1',
            'defect_type': 'functional',
            'source': 'manual',
            'assignee_id': self.developer.id,
        }
