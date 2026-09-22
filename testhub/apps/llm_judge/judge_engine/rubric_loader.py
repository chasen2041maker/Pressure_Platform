"""Rubric 加载器：Django DB 优先 → YAML fallback。

设计：
- 用户在前端配置的 Rubric 存 Django ORM（Rubric + RubricDimension + RubricRule）
- 评分引擎内部使用 judge_engine.rubric.Rubric（dataclass）
- 本加载器负责 ORM → dataclass 的转换
- DB 未命中时回退到 YAML 模板（确保不配置也能用）
"""
from __future__ import annotations

import logging
from typing import Optional

from .rubric import Rubric

logger = logging.getLogger(__name__)


def _orm_rubric_to_raw(rubric_orm) -> dict:
    """把 Django ORM Rubric 转换为 Rubric.__init__ 所需的 raw dict。"""
    dimensions = []
    for d in rubric_orm.dimensions.all().order_by('sort_order', 'id'):
        dimensions.append({
            'id': d.dim_key,
            'name': d.name,
            'type': d.dim_type,
            'weight': float(d.weight),
            'min': 1,
            'max': 5,
            'anchors': d.anchor_text or {},
            'veto': d.vetoable,
            'rule_backed': False,
            'description': '',
        })

    groups = [{
        'id': 'default',
        'name': rubric_orm.name,
        'weight': 1.0,
        'dimensions': dimensions,
    }]

    # 规则参数
    rules_dict = {}
    timeliness = {}
    numeric_gt = {}
    for r in rubric_orm.rules.all().order_by('sort_order', 'id'):
        if not r.enabled:
            continue
        params = r.params or {}
        if r.rule_key == 'absolute_words':
            rules_dict['absolute_words'] = {
                'words': params.get('keywords', []),
                'extra_regex': params.get('extra_regex', []),
                'check_quoted_context_exempt': params.get('check_quoted_context_exempt', True),
                'severity': r.severity,
                'is_veto': r.is_veto,
                'fallback_mode': r.fallback_mode,
            }
        elif r.rule_key == 'disclaimer':
            rules_dict['disclaimer'] = {
                'required_keywords': params.get('required_keywords', []),
                'allowed_patterns': params.get('allowed_patterns', []),
                'exempt_in_citations': params.get('exempt_in_citations', True),
                'severity': r.severity,
                'is_veto': r.is_veto,
                'fallback_mode': r.fallback_mode,
            }
        elif r.rule_key == 'timeliness':
            timeliness = {
                'market': params.get('market', 'A股'),
                'calendar_code': params.get('calendar_code', 'CN_A_SHARE'),
                'as_of_field': params.get('as_of_field', 'context.as_of_date'),
                'severity': r.severity,
                'is_veto': r.is_veto,
            }
        elif r.rule_key == 'numeric_gt':
            numeric_gt = {
                'default_tolerance': params.get('default_tolerance', 0.05),
                'domain_kb': params.get('domain_kb', 'financial'),
                'extract_num_llm_fallback': params.get('extract_num_llm_fallback', False),
                'severity': r.severity,
                'is_veto': r.is_veto,
            }
        elif r.rule_key == 'custom_regex':
            rules_dict.setdefault('custom_regex', []).append({
                'pattern': params.get('pattern', ''),
                'group': params.get('group', 0),
                'severity': r.severity,
                'message_template': params.get('message_template', '命中 {matched}'),
                'is_veto': r.is_veto,
            })

    return {
        'version': rubric_orm.version,
        'name': rubric_orm.name,
        'groups': groups,
        'rules': rules_dict,
        'timeliness': timeliness,
        'numeric_gt': numeric_gt,
        'scoring': rubric_orm.scoring_weights or {'rule': 0.4, 'llm': 0.6},
        'gate': rubric_orm.gate_config or {
            'green_mean': 85, 'yellow_mean': 70,
            'safety_pass_rate': 1.0, 'critical_success_rate': 0.95,
        },
        'judge_config': rubric_orm.judge_config or {},
        # 附加元数据（供 service 使用）
        '_rubric_id': rubric_orm.id,
        '_rubric_domain': rubric_orm.domain,
        # 顶层 domain/description，供 Rubric 与 Judge prompt 直接读取
        'domain': rubric_orm.domain,
        'description': rubric_orm.description or '',
    }


def load_rubric(rubric_id: Optional[int] = None, rubric_name: Optional[str] = None) -> tuple[Rubric, Optional[int]]:
    """加载 Rubric：DB 优先 → YAML fallback。

    Returns:
        (Rubric 实例, rubric_id or None)
    """
    # 1. DB 优先
    try:
        from apps.llm_judge.models import Rubric as RubricORM

        qs = RubricORM.objects.filter(is_active=True)
        if rubric_id:
            rubric_orm = qs.filter(pk=rubric_id).first()
        elif rubric_name:
            rubric_orm = qs.filter(name=rubric_name).first()
        else:
            # 默认 Rubric
            rubric_orm = qs.filter(is_default=True).first() or qs.first()

        if rubric_orm:
            raw = _orm_rubric_to_raw(rubric_orm)
            return Rubric(raw), rubric_orm.id
    except Exception as e:
        logger.warning(f'[rubric_loader] DB 加载失败，回退 YAML: {e}')

    # 2. YAML fallback（通用 qa_rubric）
    return _load_yaml_fallback(), None


def _load_yaml_fallback(domain: str = 'finance') -> Rubric:
    """从 YAML 模板加载（兼容无 DB 场景，如首次 migrate 前）。"""
    import os
    from .config import get_config

    # 优先从 settings.JUDGE_RUBRIC_DIR 找
    cfg = get_config()
    if cfg.rubric_dir and cfg.rubric_dir.exists():
        for name in (f'{domain}_rubric_v1.0.yaml', 'finance_rubric_v1.0.yaml'):
            p = cfg.rubric_dir / name
            if p.exists():
                return Rubric.load(str(p))

    # 回退到包内模板
    return Rubric.load()
