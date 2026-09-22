# -*- coding: utf-8 -*-
"""MCP 工具注册表一致性单测：防止新增工具漏登记元数据。

运行: python manage.py test apps.mcp.tests.test_registry
"""
from django.test import SimpleTestCase

from apps.mcp.registry import CATEGORY_ORDER, TOOL_REGISTRY
from apps.mcp.tools import ALL_TOOLS


class RegistryConsistencyTest(SimpleTestCase):
    def test_registry_matches_all_tools(self):
        """TOOL_REGISTRY 与 ALL_TOOLS 名称集合必须完全一致"""
        names = {fn.__name__ for fn in ALL_TOOLS}
        self.assertEqual(set(TOOL_REGISTRY), names)

    def test_meta_fields_complete(self):
        """每个工具元数据必填项完整，分类合法"""
        for meta in TOOL_REGISTRY.values():
            self.assertTrue(meta.title, f'{meta.name} 缺少 title')
            self.assertTrue(meta.summary, f'{meta.name} 缺少 summary')
            self.assertTrue(meta.description, f'{meta.name} 缺少 description')
            self.assertIn(meta.category, CATEGORY_ORDER)
            # 危险工具必须声明配对关系
            if meta.category in ('preview', 'confirm'):
                self.assertTrue(meta.paired_with, f'{meta.name} 缺少 paired_with')
                self.assertIn(meta.paired_with, TOOL_REGISTRY)

    def test_preview_confirm_pairs_symmetric(self):
        """preview <-> confirm 配对关系必须双向对称"""
        for meta in TOOL_REGISTRY.values():
            if meta.paired_with:
                self.assertEqual(TOOL_REGISTRY[meta.paired_with].paired_with, meta.name,
                                 f'{meta.name} 的配对关系不对称')

    def test_annotations_buildable(self):
        """annotations 可正常构建为 MCP 规范对象"""
        for meta in TOOL_REGISTRY.values():
            anno = meta.to_mcp_annotations()
            self.assertEqual(anno.readOnlyHint, meta.read_only)
            self.assertEqual(anno.title, meta.title)
