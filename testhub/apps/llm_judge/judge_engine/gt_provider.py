"""GroundTruth 自动匹配器：从 question 中提取公司+指标+报告期，查本地知识库。

方案优势（vs LLM 生成）：
  - 速度：毫秒级（本地 JSON 查找 + 正则），不调 API
  - 可靠：人工维护的财务数据，可审计可追溯
  - 成本：零 token 消耗

匹配流程：
  1. 从 question 中提取公司名（含别名匹配）
  2. 从 question 中提取报告期（如 "2024年报"、"2025H1"）
  3. 从 question 中提取指标关键词（如 "营收"、"净利润"）
  4. 在 financial_kb.json 中查找对应数值，组装 GroundTruth
  5. 无匹配则返回 None（跳过数值校验，LLM Judge 仍可定性评分）
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .models import GroundTruth

_DEFAULT_KB_PATH = Path(__file__).resolve().parent / 'kb' / 'financial_kb.json'


def _get_kb_path() -> Path:
    """优先返回 MEDIA 下用户可编辑的 KB 文件；不存在/不可读时回退包内默认。

    - settings.JUDGE_KB_DIR/financial_kb.json：用户可见可编辑位置（推荐用于维护）
    - 包内 kb/financial_kb.json：出厂默认兜底（settings 配置了目录但空文件时不报错）
    """
    try:
        from django.conf import settings
        d = getattr(settings, 'JUDGE_KB_DIR', None)
        if d:
            p = Path(d) / 'financial_kb.json'
            if p.exists() and p.stat().st_size > 0:
                return p
    except Exception:
        pass
    return _DEFAULT_KB_PATH


def _load_kb() -> dict:
    global _kb_cache
    if _kb_cache is None:
        kb_path = _get_kb_path()
        try:
            _kb_cache = json.loads(kb_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            # 兜底：包内默认 KB
            _kb_cache = json.loads(_DEFAULT_KB_PATH.read_text(encoding="utf-8"))
            import logging as _logging
            _logging.getLogger(__name__).warning(
                f'[gt_provider] 读取 KB {kb_path} 失败，回退包内默认 KB: {e}'
            )
    return _kb_cache


_kb_cache: dict | None = None


def _match_company(question: str, kb: dict) -> Optional[str]:
    """从 question 中匹配公司名（支持别名）。"""
    for canonical, info in kb.get("companies", {}).items():
        candidates = [canonical] + info.get("aliases", [])
        for alias in candidates:
            if alias in question:
                return canonical
    return None


def _match_period(question: str, kb: dict) -> Optional[str]:
    """从 question 中匹配报告期。"""
    for canonical, aliases in kb.get("report_periods", {}).items():
        for alias in aliases:
            if alias in question:
                return canonical
    return None


def _match_metrics(question: str, kb: dict) -> list[str]:
    """从 question 中提取涉及的指标名（支持别名）。返回标准指标名列表。"""
    matched = []
    for canonical, aliases in kb.get("metrics_aliases", {}).items():
        candidates = [canonical] + aliases
        for alias in candidates:
            if alias in question:
                if canonical not in matched:
                    matched.append(canonical)
                break
    return matched


def auto_match(question: str, context: dict | None = None) -> Optional[GroundTruth]:
    """主入口：从 question 自动匹配 ground_truth。

    Returns:
        GroundTruth（含 text + values），或 None（无匹配时）
    """
    kb = _load_kb()
    company = _match_company(question, kb)
    if not company:
        return None

    period = _match_period(question, kb)
    if not period:
        # 无报告期时取该公司第一个可用报告期（排除 aliases / 下划线开头的元信息键）
        period_keys = [k for k in kb["companies"][company]
                       if not k.startswith("_") and k != "aliases"]
        if not period_keys:
            return None
        period = period_keys[0]

    company_data = kb["companies"][company].get(period)
    if not company_data:
        return None

    metrics = _match_metrics(question, kb)
    if not metrics:
        # 问题未提到具体指标，返回该公司该期间所有数据
        metrics = list(company_data.keys())

    values = []
    text_parts = []
    for metric in metrics:
        entry = company_data.get(metric)
        if not entry:
            continue
        values.append({
            "label": f"{company}{period}{metric}",
            "value": entry["value"],
            "unit": entry["unit"],
            "tolerance": entry.get("tolerance", 5),
        })
        text_parts.append(f"{metric}{entry['value']}{entry['unit']}")

    if not values:
        return None

    text = f"{company}{period}：" + "，".join(text_parts) + "。"
    return GroundTruth(text=text, values=values)
