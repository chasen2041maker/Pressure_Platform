# -*- coding: utf-8 -*-
"""
平台内文档中心 API。

扫描项目 docs/docs-center/ 目录下的 markdown 文档，供前端渲染阅读。
- 递归暴露所有子目录中的 *.md（排除隐藏文件、非 md 文件）
- name 为相对 docs-center/ 的路径（/ 分隔）
- 路径穿越防护：拒绝 .. 片段，白名单校验 + 解析后路径必须仍在文档根内
"""
import os
import logging

from django.conf import settings
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

logger = logging.getLogger(__name__)


def _docs_dir():
    return os.path.join(settings.BASE_DIR, 'docs', 'docs-center')


def _extract_title(file_path, fallback):
    """取 markdown 首行标题（# xxx），无则用文件名"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith('#'):
                    return line.lstrip('#').strip() or fallback
                return fallback
    except Exception:
        pass
    return fallback


def list_doc_files():
    """递归扫描 docs/docs-center/ 下的 md 文件，返回 [{name, path}]。

    name 为相对文档根目录的路径（/ 分隔），path 仅内部使用不外泄。
    """
    docs_dir = _docs_dir()
    items = []
    if not os.path.isdir(docs_dir):
        return items
    for root, dirs, files in os.walk(docs_dir):
        # 跳过隐藏目录，保持目录遍历顺序稳定
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        for entry in sorted(files):
            if entry.startswith('.') or not entry.lower().endswith('.md'):
                continue
            full = os.path.join(root, entry)
            rel = os.path.relpath(full, docs_dir).replace(os.sep, '/')
            items.append({'name': rel, 'path': full})
    items.sort(key=lambda d: d['name'])
    return items


class DocsListView(APIView):
    """文档列表：GET /api/core/docs/"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        items = []
        for doc in list_doc_files():
            stat = os.stat(doc['path'])
            items.append({
                'name': doc['name'],
                'title': _extract_title(doc['path'], doc['name']),
                'size': stat.st_size,
                'updated_at': int(stat.st_mtime),
            })
        return Response({'items': items, 'total': len(items)})


class DocsContentView(APIView):
    """文档内容：GET /api/core/docs/content/?name=xxx.md"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        name = (request.query_params.get('name') or '').strip()

        # 路径穿越防护：统一分隔符后拒绝 .. 片段与绝对路径
        normalized = name.replace('\\', '/')
        if not name or '..' in normalized.split('/') or normalized.startswith('/'):
            return Response({'error': '非法的文档名称'}, status=400)

        # 白名单校验：必须真实存在于扫描结果中
        target = next((d for d in list_doc_files()
                       if d['name'] == normalized), None)
        if not target:
            return Response({'error': '文档不存在'}, status=404)

        # 双重保险：解析后的真实路径必须仍在文档根目录内
        docs_root = os.path.realpath(_docs_dir())
        real_path = os.path.realpath(target['path'])
        if os.path.commonpath([docs_root, real_path]) != docs_root:
            return Response({'error': '非法的文档名称'}, status=400)

        try:
            with open(target['path'], 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            logger.error(f"读取文档失败 {name}: {e}")
            return Response({'error': '读取文档失败'}, status=500)

        return Response({
            'name': normalized,
            'title': _extract_title(target['path'], normalized),
            'content': content,
        })
