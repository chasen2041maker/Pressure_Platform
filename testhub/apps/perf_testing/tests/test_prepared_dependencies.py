"""Persistent, project-scoped prerequisites; disposable database, no target traffic."""
from copy import deepcopy
import json
from unittest import mock

from django.test import TestCase

from apps.perf_testing import models
from apps.perf_testing.services import prepared_requests as service
from . import test_prepared_requests as fixtures


class PreparedDependencyTests(TestCase):
    setUp = fixtures.PreparedRequestTests.setUp
    project_url = fixtures.PreparedRequestTests.project_url
    upload = fixtures.PreparedRequestTests.upload
    configure = fixtures.PreparedRequestTests.configure
    prepare = fixtures.PreparedRequestTests.prepare
    edit_url = fixtures.PreparedRequestTests.edit_url
    save_edit = fixtures.PreparedRequestTests.save_edit
    import_prepared = fixtures.PreparedRequestTests.import_prepared

    def fixture(self):
        response = {'description': 'ok', 'content': {'application/json': {'schema': {
            'type': 'object', 'required': ['code'], 'properties': {'code': {'type': 'string', 'enum': ['OK']}}}}}}
        parameter = lambda name: {'name': name, 'in': 'path', 'required': True, 'schema': {'type': 'string'}}
        doc = {'openapi': '3.0.3', 'info': {'title': 'Own conversations', 'version': '1'}, 'paths': {
            '/conversations/{channel}': {'post': {'parameters': [parameter('channel')], 'responses': {'200': response}}},
            '/conversations/{id}/messages': {'get': {'parameters': [parameter('id')], 'responses': {'200': response}}},
            '/conversations/{id}/unread': {'get': {'parameters': [parameter('id')], 'responses': {'200': response}}}}}
        self.assertEqual(self.upload(doc).status_code, 201)
        self.env = self.configure()
        self.assertEqual(self.prepare().status_code, 200)
        self.producer = models.PerfPreparedRequest.objects.get(source_key='POST /conversations/{channel}')
        self.consumer = models.PerfPreparedRequest.objects.get(source_key='GET /conversations/{id}/messages')
        result = self.save_edit(self.producer.source_metadata['id'], self.producer.revision,
            {'url': '/conversations/demo', 'headers': {'Idempotency-Key': '{{request_id}}'}})
        self.assertEqual(result.status_code, 200, result.data)
        self.producer.refresh_from_db()
        self.reference = {'source_key': self.producer.source_key, 'revision': self.producer.revision,
            'definition_hash': service.definition_hash(self.producer), 'extractors': [
                {'name': 'support_conversation_id', 'type': 'JSON_PATH', 'expr': '$.data.conversation_id'}]}

    def save_dependency(self, reference=None, consumer=None, **patch):
        row = consumer or self.consumer
        suffix = 'unread' if row.source_key.endswith('/unread') else 'messages'
        return self.save_edit(row.source_metadata['id'], row.revision,
            {'url': '/conversations/{{support_conversation_id}}/' + suffix, **patch},
            {'setup_steps': [deepcopy(self.reference if reference is None else reference)]})

    def ready(self):
        self.fixture()
        saved = self.save_dependency()
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['prepared']['status'], 'unverified', saved.data)
        self.consumer.refresh_from_db()
        return saved

    def test_save_and_reprepare_preserve_dependency_and_expose_write_closure(self):
        saved = self.ready()
        self.assertIn('support_conversation_id', saved.data['known_variable_names'])
        self.assertEqual(saved.data['preparation']['setup_steps'], [self.reference])
        summary = saved.data['prepared']
        self.assertTrue(summary['has_writes'])
        self.assertEqual(summary['verification_methods'], ['POST', 'GET'])
        self.assertEqual(summary['setup_steps'][0]['path'], '/conversations/{channel}')
        self.assertEqual(summary['setup_steps'][0]['outputs'], ['support_conversation_id'])
        self.assertEqual(summary['definition_hash'], service.definition_hash(self.consumer))
        original = deepcopy(self.consumer.preparation)
        revision = self.consumer.revision
        result = self.prepare([self.consumer.source_metadata['id']])
        self.assertEqual(result.status_code, 200, result.data)
        self.consumer.refresh_from_db()
        self.assertEqual(self.consumer.preparation, original)
        self.assertEqual(self.consumer.revision, revision)
        self.assertEqual(models.PerfExecution.objects.count(), 0)

    def test_unknown_outputs_stay_blocked_and_omitted_dependency_cannot_silently_remove_it(self):
        self.ready()
        saved = self.save_edit(self.consumer.source_metadata['id'], self.consumer.revision,
            {'url': '/conversations/{{not_produced}}/messages'}, {'setup_steps': [self.reference]})
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['prepared']['status'], 'blocked')
        self.consumer.refresh_from_db()
        before = deepcopy(self.consumer.preparation)
        saved = self.save_edit(self.consumer.source_metadata['id'], self.consumer.revision, {'params': {'limit': 10}})
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['preparation']['setup_steps'], before['setup_steps'])

    def test_dependency_schema_is_strict_and_atomic(self):
        self.fixture()
        invalid = [None, {}, 'bad', [self.reference] * 9]
        bad_refs = [dict(self.reference, revision=True), dict(self.reference, revision=0),
            dict(self.reference, definition_hash='wrong'), dict(self.reference, url='/arbitrary'),
            dict(self.reference, extractors=[]), dict(self.reference, extractors=[
                {'name': 'support_conversation_id', 'type': 'REGEX', 'expr': '.*'}]),
            dict(self.reference, extractors=[{'name': 'x', 'type': 'JSON_PATH', 'expr': '$.items[*].id'}])]
        before = models.PerfPreparedRequest.objects.filter(pk=self.consumer.pk).values().get()
        for value in invalid + [[ref] for ref in bad_refs]:
            with self.subTest(value=value):
                result = self.save_edit(self.consumer.source_metadata['id'], self.consumer.revision, {}, {'setup_steps': value})
                self.assertEqual(result.status_code, 400, result.data)
                self.assertEqual(models.PerfPreparedRequest.objects.filter(pk=self.consumer.pk).values().get(), before)

    def test_protected_and_colliding_outputs_are_rejected(self):
        self.fixture()
        self.env.variables = [{'name': 'tenant_choice', 'type': 'CONSTANT', 'value': 'fixed'}]
        self.env.save()
        self.assertEqual(self.prepare().status_code, 200)
        self.producer.refresh_from_db(); self.consumer.refresh_from_db()
        self.reference.update(revision=self.producer.revision, definition_hash=service.definition_hash(self.producer))
        for name in ('token', 'user_id', 'device_id', 'request_id', '__proto__', 'tenant_choice'):
            ref = deepcopy(self.reference); ref['extractors'][0]['name'] = name
            with self.subTest(name=name):
                self.assertEqual(self.save_dependency(ref).status_code, 400)
        ref = deepcopy(self.reference); ref['extractors'] *= 2
        self.assertEqual(self.save_dependency(ref).status_code, 400)
        self.assertEqual(self.save_dependency(extractors=deepcopy(self.reference['extractors'])).status_code, 400)

    def test_missing_foreign_self_nested_and_stale_dependencies_are_rejected(self):
        self.fixture()
        for ref in (dict(self.reference, source_key='GET /outside'),
                    dict(self.reference, revision=self.producer.revision + 1),
                    dict(self.reference, definition_hash='0' * 64),
                    dict(self.reference, source_key=self.consumer.source_key)):
            with self.subTest(ref=ref):
                self.assertIn(self.save_dependency(ref).status_code, (400, 409))
        other = deepcopy(self.producer)
        other.pk = None; other.project = self.other; other.source_key = 'GET /outside'; other.save()
        self.assertEqual(self.save_dependency(dict(self.reference, source_key=other.source_key)).status_code, 409)
        self.producer.preparation['setup_steps'] = [self.reference]; self.producer.save()
        self.reference['definition_hash'] = service.definition_hash(self.producer)
        self.assertEqual(self.save_dependency().status_code, 400)

    def test_dependency_changes_invalidate_consumer_without_replacing_original_binding(self):
        self.ready()
        original = deepcopy(self.consumer.preparation)
        original_hash = service.definition_hash(self.consumer)
        changed = self.save_edit(self.producer.source_metadata['id'], self.producer.revision, {'params': {'new': 'value'}})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(service.public_summary(self.consumer)['status'], 'stale')
        self.assertEqual(service.definition_hash(self.consumer), original_hash)
        self.assertEqual(self.prepare([self.consumer.source_metadata['id']]).status_code, 409)
        self.consumer.refresh_from_db()
        self.assertEqual(self.consumer.preparation, original)
        self.producer.refresh_from_db()
        self.reference.update(revision=self.producer.revision, definition_hash=service.definition_hash(self.producer))
        self.assertEqual(self.save_dependency().status_code, 200)

    def test_fresh_scenes_automatically_import_same_setup_and_deduplicate_exact_reimport(self):
        self.ready()
        for name in ('First', 'Second'):
            scene = models.PerfScenario.objects.create(project=self.project, name=name, created_by=self.owner, engine='K6')
            with mock.patch('apps.perf_testing.services.pool_verification.verification_summary',
                            return_value={'status': 'passed', 'last_evidence': None}):
                result = self.import_prepared(scene, [self.consumer])
                self.assertEqual(result.status_code, 201, result.data)
                steps = list(scene.steps.order_by('order'))
                self.assertEqual(len(steps), 2)
                self.assertTrue(steps[0].is_setup)
                self.assertEqual(steps[0].url, '/conversations/demo')
                self.assertEqual(steps[0].extractors, self.reference['extractors'])
                self.assertEqual(steps[0].headers['Idempotency-Key'], '{{request_id}}')
                self.assertFalse(steps[1].is_setup)
                self.assertNotIn('setup_steps', steps[1].preparation)
                result = self.import_prepared(scene, [self.consumer])
                self.assertEqual(result.status_code, 201, result.data)
                self.assertEqual(scene.steps.filter(is_setup=True).count(), 1)
                self.assertEqual(scene.steps.filter(is_setup=False).count(), 2)

    def test_shared_setup_is_deduplicated_but_manual_output_conflict_fails_atomically(self):
        self.ready()
        second = models.PerfPreparedRequest.objects.get(source_key='GET /conversations/{id}/unread')
        self.assertEqual(self.save_dependency(consumer=second).status_code, 200)
        second.refresh_from_db()
        self.assertEqual(len(service.expanded_setup_steps([self.consumer, second])), 1)
        scene = models.PerfScenario.objects.create(project=self.project, name='Conflict', created_by=self.owner,
            **service.scenario_kwargs(self.consumer))
        models.PerfScenarioStep.objects.create(scenario=scene, name='Manual producer', url='/manual',
            is_setup=True, extractors=deepcopy(self.reference['extractors']))
        before = models.PerfScenario.objects.filter(pk=scene.pk).values().get()
        with mock.patch('apps.perf_testing.services.pool_verification.verification_summary',
                        return_value={'status': 'passed', 'last_evidence': None}):
            result = self.import_prepared(scene, [self.consumer, second])
        self.assertEqual(result.status_code, 409, result.data)
        self.assertEqual(scene.steps.count(), 1)
        self.assertEqual(models.PerfScenario.objects.filter(pk=scene.pk).values().get(), before)

    def test_public_dependency_summary_never_exposes_request_values(self):
        self.ready()
        self.producer.request['headers']['Authorization'] = 'private-producer-token'
        self.producer.save()
        self.reference['definition_hash'] = service.definition_hash(self.producer)
        self.assertEqual(self.save_dependency().status_code, 200)
        self.consumer.refresh_from_db()
        summary = service.public_summary(self.consumer)
        self.assertNotIn('private-producer-token', json.dumps(summary))
        self.assertNotIn('demo', json.dumps(summary['setup_steps']))

    def test_distinct_edges_from_one_producer_have_distinct_public_keys(self):
        self.fixture()
        self.assertEqual(self.producer.request.get('extractors'), [])
        second = deepcopy(self.reference)
        second['extractors'][0]['name'] = 'other_conversation_id'
        saved = self.save_edit(self.consumer.source_metadata['id'], self.consumer.revision,
            {'url': '/conversations/{{support_conversation_id}}/messages'},
            {'setup_steps': [self.reference, second]})
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['prepared']['status'], 'unverified')
        self.consumer.refresh_from_db()
        summary = saved.data['prepared']['setup_steps']
        resolved = service.expanded_setup_steps([self.consumer])
        self.assertEqual(len(resolved), 2)
        self.assertEqual([step['key'] for step in summary], [step['key'] for step in resolved])
        self.assertNotEqual(summary[0]['key'], summary[1]['key'])
        self.assertEqual(summary[0]['source_key'], summary[1]['source_key'])
        self.assertEqual(summary[0]['definition_hash'], summary[1]['definition_hash'])

    def test_identical_edge_across_consumers_shares_one_public_key(self):
        first = self.ready()
        second = models.PerfPreparedRequest.objects.get(source_key='GET /conversations/{id}/unread')
        saved = self.save_dependency(consumer=second)
        self.assertEqual(saved.status_code, 200, saved.data)
        second.refresh_from_db()
        first_key = first.data['prepared']['setup_steps'][0]['key']
        self.assertEqual(first_key, saved.data['prepared']['setup_steps'][0]['key'])
        resolved = service.expanded_setup_steps([self.consumer, second])
        self.assertEqual([step['key'] for step in resolved], [first_key])

    def test_dependency_context_and_source_changes_fail_closed(self):
        self.ready()
        from apps.api_testing.models import ApiRequest
        ApiRequest.objects.filter(pk=self.producer.source_metadata['id']).update(params={'changed': True})
        self.assertEqual(service.public_summary(self.consumer)['status'], 'stale')
        self.assertEqual(self.save_dependency().status_code, 409)
        self.producer.refresh_from_db()
        self.producer.context = {**self.producer.context, 'account_pool_group': 'another-group'}
        self.producer.context_fingerprint = self.service.digest(self.producer.context)
        self.producer.save()
        self.reference['definition_hash'] = service.definition_hash(self.producer)
        self.assertEqual(self.save_dependency().status_code, 409)

    def test_explicit_dependency_removal_cannot_leave_output_known(self):
        self.ready()
        result = self.save_edit(self.consumer.source_metadata['id'], self.consumer.revision, {}, {'setup_steps': []})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['prepared']['status'], 'blocked')
        self.assertNotIn('support_conversation_id', result.data['known_variable_names'])
        self.assertEqual(result.data['prepared']['setup_steps'], [])
        self.assertFalse(result.data['prepared']['has_writes'])

    def test_incomplete_http_only_disabled_and_auth_overwriting_producers_cannot_be_used(self):
        self.fixture()
        original_request = deepcopy(self.producer.request)
        original_gaps = deepcopy(self.producer.gaps)
        for changed in ('blocked', 'http_only', 'disabled', 'setup', 'auth_output'):
            with self.subTest(changed=changed):
                self.producer.request = deepcopy(original_request)
                self.producer.gaps = deepcopy(original_gaps)
                if changed == 'blocked': self.producer.gaps = [{'code': 'required_value'}]
                elif changed == 'http_only': self.producer.request['assertions'] = [{'type': 'STATUS_CODE', 'expected': 200}]
                elif changed == 'disabled': self.producer.request['enabled'] = False
                elif changed == 'setup': self.producer.request['is_setup'] = True
                else: self.producer.request['extractors'] = [{'name': 'token', 'type': 'JSON_PATH', 'expr': '$.token'}]
                self.producer.save()
                self.reference['definition_hash'] = service.definition_hash(self.producer)
                self.assertEqual(self.save_dependency().status_code, 400)

    def test_dependency_listing_resolves_private_bindings_once_in_readonly_scope(self):
        self.ready()
        with mock.patch.object(service, '_context', wraps=service._context) as context:
            summary = service.public_summary(self.consumer)
            self.assertEqual(summary['status'], 'unverified')
            self.assertEqual(context.call_count, 1)

    def test_import_rejects_mutated_existing_setup_and_stale_consumer_revision(self):
        self.ready()
        scene = models.PerfScenario.objects.create(project=self.project, name='Exact', created_by=self.owner, engine='K6')
        with mock.patch('apps.perf_testing.services.pool_verification.verification_summary',
                        return_value={'status': 'passed', 'last_evidence': None}):
            self.assertEqual(self.import_prepared(scene, [self.consumer]).status_code, 201)
            setup = scene.steps.get(is_setup=True)
            setup.url = '/changed'; setup.save()
            self.assertEqual(self.import_prepared(scene, [self.consumer]).status_code, 409)
            self.assertEqual(scene.steps.count(), 2)
            self.assertEqual(self.import_prepared(scene, [self.consumer],
                expected_prepared_revisions={self.consumer.source_key: self.consumer.revision - 1}).status_code, 409)

    def test_selected_producer_keeps_its_separate_business_step_with_identical_output(self):
        self.fixture()
        saved = self.save_edit(self.producer.source_metadata['id'], self.producer.revision,
            {'extractors': deepcopy(self.reference['extractors'])})
        self.assertEqual(saved.status_code, 200, saved.data)
        self.producer.refresh_from_db()
        self.reference.update(revision=self.producer.revision, definition_hash=service.definition_hash(self.producer))
        self.assertEqual(self.save_dependency().status_code, 200)
        self.consumer.refresh_from_db()
        dependencies = service.expanded_setup_steps([self.producer, self.consumer])
        self.assertEqual(len(dependencies), 1)
        self.assertEqual(dependencies[0]['kwargs']['extractors'], self.reference['extractors'])
        scene = models.PerfScenario.objects.create(project=self.project, name='Full selection', created_by=self.owner, engine='K6')
        with mock.patch('apps.perf_testing.services.pool_verification.verification_summary',
                        return_value={'status': 'passed', 'last_evidence': None}):
            self.assertEqual(self.import_prepared(scene, [self.producer, self.consumer]).status_code, 201)
            self.assertEqual(scene.steps.filter(is_setup=True).count(), 1)
            self.assertEqual(scene.steps.filter(is_setup=False).count(), 2)
            self.assertEqual(self.import_prepared(scene, [self.consumer]).status_code, 201)
            self.assertEqual(scene.steps.filter(is_setup=True).count(), 1)

    def test_empty_catalog_prepare_remains_readonly_and_empty(self):
        self.configure()
        result = self.prepare(version=0)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['prepared_count'], 0)
        self.assertEqual(models.PerfPreparedRequest.objects.count(), 0)

    def native_producer(self):
        self.fixture()
        self.assertEqual(self.save_edit(self.producer.source_metadata['id'], self.producer.revision,
            {'extractors': deepcopy(self.reference['extractors'])}).status_code, 200)
        self.producer.refresh_from_db()
        self.reference.update(revision=self.producer.revision, definition_hash=service.definition_hash(self.producer))
        self.assertEqual(self.save_dependency().status_code, 200)
        self.consumer.refresh_from_db()

    def test_later_business_import_cannot_overwrite_existing_setup_output(self):
        self.ready()
        intruder = models.PerfPreparedRequest.objects.get(source_key='GET /conversations/{id}/unread')
        result = self.save_edit(intruder.source_metadata['id'], intruder.revision,
            {'url': '/conversations/other/unread', 'extractors': [
                {'name': 'support_conversation_id', 'type': 'JSON_PATH', 'expr': '$.data.other_id'}]})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['prepared']['status'], 'unverified')
        intruder.refresh_from_db()
        scene = models.PerfScenario.objects.create(project=self.project, name='Sequential import', created_by=self.owner, engine='K6')
        with mock.patch('apps.perf_testing.services.pool_verification.verification_summary',
                        return_value={'status': 'passed', 'last_evidence': None}):
            self.assertEqual(self.import_prepared(scene, [self.consumer]).status_code, 201)
            before_scene = models.PerfScenario.objects.filter(pk=scene.pk).values().get()
            before_steps = list(scene.steps.order_by('pk').values())
            rejected = self.import_prepared(scene, [intruder])
            self.assertEqual(rejected.status_code, 409, rejected.data)
        self.assertEqual(models.PerfScenario.objects.filter(pk=scene.pk).values().get(), before_scene)
        self.assertEqual(list(scene.steps.order_by('pk').values()), before_steps)

    def test_same_original_producer_and_consumer_can_be_imported_in_either_order(self):
        self.native_producer()
        for selected in ((self.producer, self.consumer), (self.consumer, self.producer)):
            with self.subTest(order=[row.source_key for row in selected]):
                scene = models.PerfScenario.objects.create(project=self.project, name='Separate imports', created_by=self.owner, engine='K6')
                with mock.patch('apps.perf_testing.services.pool_verification.verification_summary',
                                return_value={'status': 'passed', 'last_evidence': None}):
                    for row in selected:
                        result = self.import_prepared(scene, [row])
                        self.assertEqual(result.status_code, 201, result.data)
                self.assertEqual(scene.steps.filter(is_setup=True).count(), 1)
                self.assertEqual(scene.steps.filter(is_setup=False).count(), 2)

    def test_later_original_producer_requires_unchanged_definition_and_frozen_marker(self):
        self.native_producer()
        for mutation in ('missing_marker', 'changed_marker', 'changed_setup', 'changed_producer'):
            with self.subTest(mutation=mutation):
                scene = models.PerfScenario.objects.create(project=self.project, name='Bound original', created_by=self.owner, engine='K6')
                with mock.patch('apps.perf_testing.services.pool_verification.verification_summary',
                                return_value={'status': 'passed', 'last_evidence': None}):
                    self.assertEqual(self.import_prepared(scene, [self.consumer]).status_code, 201)
                    setup = scene.steps.get(is_setup=True)
                    if mutation == 'missing_marker':
                        setup.source_metadata.pop('_prepared_setup', None)
                    elif mutation == 'changed_marker':
                        setup.source_metadata['_prepared_setup'] = {'definition_hash': '0' * 64}
                    elif mutation == 'changed_setup':
                        setup.params = {'changed': 'yes'}
                    else:
                        self.assertEqual(self.save_edit(self.producer.source_metadata['id'], self.producer.revision,
                            {'params': {'changed': 'yes'}}).status_code, 200)
                        self.producer.refresh_from_db()
                    setup.save()
                    before = list(scene.steps.order_by('pk').values())
                    self.assertEqual(self.import_prepared(scene, [self.producer]).status_code, 409)
                    self.assertEqual(list(scene.steps.order_by('pk').values()), before)
