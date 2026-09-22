"""评分缓存：Django cache 封装（替代原 cachetools + prometheus）。

缓存键 = hash(question + answer + gt_values + rubric_version + judge_model)
缓存值 = JudgeVerdict dict
指标 = django cache 计数器（替代 prometheus Counter）
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CACHE_PREFIX = 'llm_judge:verdict:'
_METRIC_HITS = 'llm_judge:metric:cache_hits'
_METRIC_MISSES = 'llm_judge:metric:cache_misses'


def _get_cache():
    """获取 Django cache 实例（懒加载，兼容非 Django 环境）。"""
    try:
        from django.core.cache import cache
        return cache
    except Exception:
        return None


def _incr(name: str, amount: int = 1):
    """缓存计数器自增（兼容 LocMemCache 不支持 incr 的情况）。"""
    c = _get_cache()
    if c is None:
        return
    try:
        c.incr(name, amount)
    except (ValueError, KeyError):
        # LocMemCache 在 key 不存在时 incr 抛 ValueError，需要先 get 再 set
        try:
            current = c.get(name, 0) + amount
            c.set(name, current, timeout=None)
        except Exception:
            pass


def build_cache_key(question: str, answer: str, gt_values: list, rubric_version: str, judge_model: str) -> str:
    """构造缓存键：5 因子完全相同才命中。"""
    gt_normalized = sorted([json.dumps(v, sort_keys=True, ensure_ascii=False) for v in (gt_values or [])])
    raw = f'{question}|{answer}|{json.dumps(gt_normalized, ensure_ascii=False)}|{rubric_version}|{judge_model}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def get_cached(key: str) -> Optional[Any]:
    """读缓存，命中/未命中自动计数。"""
    c = _get_cache()
    if c is None:
        return None
    try:
        val = c.get(_CACHE_PREFIX + key)
        if val is not None:
            _incr(_METRIC_HITS)
            return val
        _incr(_METRIC_MISSES)
        return None
    except Exception as e:
        logger.warning(f'[JudgeCache] get 失败: {e}')
        return None


def set_cached(key: str, value: Any, timeout: Optional[int] = None):
    """写缓存。"""
    c = _get_cache()
    if c is None:
        return
    try:
        from django.conf import settings
        ttl = timeout if timeout is not None else getattr(settings, 'JUDGE_CACHE_TIMEOUT', 3600)
        c.set(_CACHE_PREFIX + key, value, timeout=ttl)
    except Exception as e:
        logger.warning(f'[JudgeCache] set 失败: {e}')


def get_cache_metrics() -> dict:
    """获取缓存指标（Dashboard 用）。"""
    c = _get_cache()
    if c is None:
        return {'hits': 0, 'misses': 0, 'hit_rate': 0.0}
    hits = c.get(_METRIC_HITS, 0) or 0
    misses = c.get(_METRIC_MISSES, 0) or 0
    total = hits + misses
    return {
        'hits': hits,
        'misses': misses,
        'hit_rate': hits / total if total > 0 else 0.0,
    }


def clear_cache():
    """清空缓存（调试用）。"""
    c = _get_cache()
    if c is None:
        return
    try:
        c.delete_many([_METRIC_HITS, _METRIC_MISSES])
        # LocMemCache 不支持按前缀删除，只能整个清（保守做法：只清计数器）
        logger.info('[JudgeCache] 计数器已清空')
    except Exception as e:
        logger.warning(f'[JudgeCache] clear 失败: {e}')
