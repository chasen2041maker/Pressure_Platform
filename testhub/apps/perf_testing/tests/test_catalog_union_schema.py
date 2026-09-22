"""Request schema regression tests; all values are local synthetic fixtures."""
from copy import deepcopy
import json

from django.test import SimpleTestCase, TestCase

from apps.perf_testing.services import api_catalog as catalog
from apps.perf_testing import models
from . import test_prepared_requests as prepared_fixtures
from . import test_api_catalog as fixtures


class UnionRequestSchemaTests(SimpleTestCase):
    def readiness(self, schema, value, media_type='application/json', **preparation):
        document = {'openapi': '3.1.0', 'info': {'title': 'Schema fixtures', 'version': '1'},
            'paths': {'/fixture': {'post': {'requestBody': {'required': True, 'content': {
                media_type: {'schema': schema}}}, 'responses': {'200': {'description': 'OK'}}}}}}
        operation = catalog.parse_document(document)['operations'][0]
        request = {**deepcopy(operation['request']), 'body': json.dumps(value)}
        return catalog.request_readiness(request, operation, preparation)

    def test_form_union_uses_actual_fields_and_rejects_invalid_branches(self):
        schema = {'type': 'object', 'required': ['value'], 'properties': {'value': {
            'oneOf': [{'type': 'string'}, {'type': 'integer'}]}}}
        for media in ('application/x-www-form-urlencoded', 'multipart/form-data'):
            with self.subTest(media=media):
                for value in ('actual', 7, ''):
                    self.assertTrue(self.readiness(schema, {'value': value}, media)['ready'])
                for value in ({'value': True}, {'value': None}, {}):
                    self.assertFalse(self.readiness(schema, value, media)['ready'])
                ambiguous = deepcopy(schema)
                ambiguous['properties']['value']['oneOf'] = [{'type': 'number'}, {'type': 'integer'}]
                self.assertFalse(self.readiness(ambiguous, {'value': 7}, media)['ready'])

    def test_form_empty_string_still_honors_selected_schema_constraints(self):
        schema = {'type': 'object', 'required': ['value'], 'properties': {'value': {'type': 'string'}}}
        for media in ('application/x-www-form-urlencoded', 'multipart/form-data'):
            with self.subTest(media=media):
                self.assertTrue(self.readiness(schema, {'value': ''}, media)['ready'])
                constrained = deepcopy(schema)
                constrained['properties']['value']['minLength'] = 1
                self.assertFalse(self.readiness(constrained, {'value': ''}, media)['ready'])

    def test_native_extracted_number_and_stock_template_are_deferred_but_mapping_required(self):
        duration = {'type': 'object', 'required': ['duration_seconds'], 'properties': {
            'duration_seconds': {'type': 'integer', 'minimum': 1, 'maximum': 604800}}}
        body = {'duration_seconds': '{{duration}}'}
        self.assertTrue(self.readiness(duration, body, _known_extractors=['duration'])['ready'])
        self.assertFalse(self.readiness(duration, body)['ready'])
        self.assertFalse(self.readiness(duration, {'duration_seconds': '100'})['ready'])
        stock = {'type': 'array', 'items': {'type': 'string', 'pattern': '^(sh[69][0-9]{5}|sz[023][0-9]{5})$',
            'not': {'pattern': '^(?:sh000|sz399)'}}}
        self.assertTrue(self.readiness(stock, ['{{stock}}'], _known_extractors=['stock'])['ready'])
        self.assertTrue(self.readiness(stock, ['sz000001'])['ready'])
        self.assertFalse(self.readiness(stock, ['sz399001'])['ready'])

    def test_nested_array_union_matches_actual_element_instead_of_root(self):
        schema = {'type': 'object', 'required': ['items'], 'properties': {'items': {
            'type': 'array', 'items': {'anyOf': [{'type': 'string'}, {'type': 'number'}]}}}}
        for value in ({'items': ['ready', 2]}, {'items': []}):
            self.assertTrue(self.readiness(schema, value)['ready'])
        self.assertFalse(self.readiness(schema, {'items': [False]})['ready'])

    def test_oneof_requires_exactly_one_matching_branch_and_checks_siblings(self):
        schema = {'type': 'object', 'required': ['id'], 'properties': {'id': {'type': 'integer'}},
            'oneOf': [{'required': ['cat']}, {'required': ['dog']}]}
        self.assertTrue(self.readiness(schema, {'id': 2, 'cat': True})['ready'])
        for value in ({'id': 2}, {'id': 2, 'cat': True, 'dog': True}, {'cat': True}, {'id': '2', 'cat': True}):
            self.assertFalse(self.readiness(schema, value, body_reviewed=True)['ready'], value)

    def test_primitive_types_consts_and_anyof_cardinality_are_strict(self):
        self.assertTrue(self.readiness({'anyOf': [{'type': 'integer'}, {'type': 'number'}]}, 2)['ready'])
        self.assertFalse(self.readiness({'oneOf': [{'type': 'integer'}, {'type': 'number'}]}, 2)['ready'])
        for schema, invalid in [({'type': 'integer', 'const': 1}, True),
                                ({'type': 'number'}, False), ({'type': 'boolean'}, 0),
                                ({'type': 'string'}, 5), ({'type': 'null'}, 'null')]:
            self.assertFalse(self.readiness(schema, invalid)['ready'], (schema, invalid))

    def test_required_nullable_reminder_validity_accepts_present_null(self):
        schema = {'type': 'object', 'required': ['validity'], 'properties': {'validity': {
            'type': 'object', 'required': ['mode', 'valid_until'], 'properties': {
                'mode': {'type': 'string', 'enum': ['permanent', 'until_date']},
                'valid_until': {'type': ['string', 'null']}}}}}
        self.assertTrue(self.readiness(schema, {'validity': {'mode': 'permanent', 'valid_until': None}})['ready'])
        for value in ({'validity': {'mode': 'permanent'}}, {'validity': {'mode': 'permanent', 'valid_until': 1}}):
            self.assertFalse(self.readiness(schema, value)['ready'])

    def test_watermark_union_not_required_and_parent_constraints(self):
        ack = {'type': 'object', 'additionalProperties': False, 'required': ['scope', 'target', 'through'],
            'properties': {'scope': {'type': 'string'}, 'target': {'type': 'string'},
                'through': {'type': 'string', 'pattern': '^[1-9][0-9]*$'}, 'conversation_id': {'type': 'string'}},
            'oneOf': [
                {'properties': {'scope': {'enum': ['system', 'comment']}, 'target': {'const': 'scope'}},
                    'not': {'required': ['conversation_id']}},
                {'required': ['conversation_id'], 'properties': {
                    'scope': {'const': 'conversation'}, 'target': {'const': 'conversation'}}}]}
        schema = {'type': 'object', 'required': ['acks'], 'properties': {'acks': {
            'type': 'array', 'minItems': 1, 'maxItems': 2, 'items': ack}}}
        valid = {'scope': 'system', 'target': 'scope', 'through': '123'}
        self.assertTrue(self.readiness(schema, {'acks': [valid]})['ready'])
        for value in ([], [valid] * 3, [{**valid, 'conversation_id': '7'}], [{**valid, 'through': 123}],
                      [{**valid, 'through': '0'}], [{**valid, 'extra': 'no'}], [{'scope': 'system', 'target': 'scope'}]):
            self.assertFalse(self.readiness(schema, {'acks': value}, body_reviewed=True)['ready'], value)

    def test_optional_union_is_not_required_and_unknown_schema_stays_blocked(self):
        schema = {'type': 'object', 'properties': {'optional': {'oneOf': [{'type': 'string'}, {'type': 'null'}]}}}
        self.assertTrue(self.readiness(schema, {})['ready'])
        self.assertTrue(self.readiness(schema, {'optional': None})['ready'])
        self.assertFalse(self.readiness({'oneOf': [{'type': 'unsupported'}, {'type': 'integer'}]}, True)['ready'])

    def test_malformed_branch_cannot_be_hidden_by_a_matching_branch(self):
        for keyword in ('oneOf', 'anyOf'):
            self.assertFalse(self.readiness({keyword: [{'type': 'unsupported'}, {'type': 'integer'}]}, 1)['ready'])

    def test_nullable_objects_and_object_only_keywords_keep_json_semantics(self):
        nullable = {'type': ['object', 'null'], 'required': ['id'], 'properties': {'id': {'type': 'integer'}}}
        self.assertTrue(self.readiness(nullable, None)['ready'])
        self.assertFalse(self.readiness(nullable, {})['ready'])
        self.assertFalse(self.readiness({'oneOf': [{'required': ['a']}, {'required': ['b']}]}, 'text')['ready'])
        self.assertFalse(self.readiness({'not': {'required': ['a']}}, 'text')['ready'])

    def test_additional_properties_apply_to_nullable_and_implicit_objects(self):
        for kind in ({'type': ['object', 'null']}, {}):
            self.assertFalse(self.readiness({**kind, 'additionalProperties': False}, {'extra': 1})['ready'])
            schema = {**kind, 'additionalProperties': {'type': 'integer'}}
            self.assertFalse(self.readiness(schema, {'extra': 'wrong'})['ready'])
            self.assertTrue(self.readiness(schema, {'extra': 1})['ready'])

    def test_openapi_nullable_array_preserves_null_and_checks_present_items(self):
        schema = {'type': 'array', 'nullable': True, 'items': {'type': 'integer'}}
        self.assertTrue(self.readiness(schema, None)['ready'])
        self.assertTrue(self.readiness(schema, [1])['ready'])
        self.assertFalse(self.readiness(schema, ['wrong'])['ready'])

    def test_branch_example_confirmation_and_empty_string_constraints_remain(self):
        schema = {'oneOf': [{'type': 'object', 'required': ['id'],
            'properties': {'id': {'type': 'string', 'example': 'sample-id', 'minLength': 1}}}, {'type': 'null'}]}
        self.assertFalse(self.readiness(schema, {'id': 'sample-id'})['ready'])
        self.assertTrue(self.readiness(schema, {'id': 'sample-id'}, confirmed_fields=['body/id'])['ready'])
        self.assertFalse(self.readiness(schema, {'id': ''})['ready'])
        self.assertTrue(self.readiness({'type': 'object', 'required': ['value'],
            'properties': {'value': {'type': 'string'}}}, {'value': ''})['ready'])


class UnionPreparedRequestApiTests(TestCase):
    setUp = fixtures.ApiCatalogTests.setUp
    project_url = fixtures.ApiCatalogTests.project_url
    upload = fixtures.ApiCatalogTests.upload
    configure = prepared_fixtures.PreparedRequestTests.configure
    prepare = prepared_fixtures.PreparedRequestTests.prepare
    save_edit = prepared_fixtures.PreparedRequestTests.save_edit
    edit_url = prepared_fixtures.PreparedRequestTests.edit_url

    def test_normal_editor_rechecks_request_but_never_grants_verification(self):
        doc = fixtures.contract()
        doc['components']['schemas']['Input'] = {'type': 'object', 'required': ['acks'], 'properties': {
            'acks': {'type': 'array', 'minItems': 1, 'items': {'oneOf': [
                {'type': 'object', 'required': ['scope', 'through'], 'additionalProperties': False,
                    'properties': {'scope': {'const': 'system'}, 'through': {'type': 'string'}}},
                {'type': 'object', 'required': ['scope', 'conversation_id'],
                    'properties': {'scope': {'const': 'conversation'}, 'conversation_id': {'type': 'string'}}}]}}}}
        self.assertEqual(self.upload(doc).status_code, 201)
        self.configure()
        self.assertEqual(self.prepare().status_code, 200)
        row = models.PerfPreparedRequest.objects.get(source_key='POST /items')
        valid = self.save_edit(row.source_metadata['id'], row.revision, {
            'body': json.dumps({'acks': [{'scope': 'system', 'through': '42'}]}),
            'assertions': [{'type': 'STATUS_CODE', 'expected': 201},
                           {'type': 'JSON_PATH', 'json_path': '$.code', 'expected': 'OK'}]})
        self.assertEqual(valid.status_code, 200, valid.data)
        self.assertEqual(valid.data['prepared']['status'], 'unverified', valid.data)
        self.assertEqual(valid.data['prepared']['gaps'], [])
        self.assertIsNone(valid.data['prepared']['last_evidence'])
        invalid = self.save_edit(row.source_metadata['id'], valid.data['prepared']['revision'], {
            'body': json.dumps({'acks': [{'scope': 'system', 'through': True}]})}, {'body_reviewed': True})
        self.assertEqual(invalid.status_code, 200, invalid.data)
        self.assertEqual(invalid.data['prepared']['status'], 'blocked')
        self.assertTrue(any(issue['code'] == 'schema_choice' for issue in invalid.data['prepared']['gaps']))
        self.assertEqual(models.PerfExecution.objects.count(), 0)
