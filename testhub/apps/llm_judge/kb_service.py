"""知识库服务：
1. 把数据库维护的结构化 KB 导出为 financial_kb.json 格式（供 gt_provider 消费，保留兼容）
2. 从非结构化文本解析出 companies/periods/metrics/values 结构化数据，供界面预览和批量写入
"""
from __future__ import annotations

import json
import re
import logging
from pathlib import Path
from typing import Optional

from django.conf import settings
from django.db.models import Count

from .models import KnowledgeBase, KBCompany, KBReportPeriod, KBMetric, KBMetricValue

logger = logging.getLogger(__name__)

UNIT_ALIASES = {
    '亿': ['亿', '亿元', '亿人民币', '亿RMB'],
    '万': ['万', '万元', '万人民币'],
    '%': ['%', 'pct', '百分点', '百分比'],
    '万辆': ['万辆', '万台'],
}


def _normalize_unit(u: str) -> str:
    u = (u or '').strip()
    if not u:
        return ''
    for canonical, aliases in UNIT_ALIASES.items():
        if u in aliases:
            return canonical
    return u


def _matches_alias(text: str, aliases: list[str], canonical: str) -> bool:
    if not text:
        return False
    if canonical and canonical in text:
        return True
    for a in aliases or []:
        if a and a in text:
            return True
    return False


# ============================================================
# DB → JSON 导出（同步到 MEDIA 目录，供 gt_provider.py 使用）
# ============================================================
def export_kb_to_json(kb_id: Optional[int] = None) -> Path:
    """将 KB 数据导出为 financial_kb.json 到 JUDGE_KB_DIR。

    若指定 kb_id，则优先导出该 KB；否则导出 is_default=True 的 KB。
    返回导出的文件路径。
    """
    qs = KnowledgeBase.objects.filter(is_active=True)
    if kb_id:
        kb = qs.filter(pk=kb_id).first()
    else:
        kb = qs.filter(is_default=True).first() or qs.first()

    if not kb:
        raise ValueError('没有可用的知识库，请先在界面创建。')

    data = {
        '_meta': {
            'description': kb.description or f'{kb.name} - 从界面维护导出',
            'maintainer': getattr(kb.updated_by, 'username', '') if kb.updated_by else '',
            'kb_id': kb.id,
            'kb_name': kb.name,
            'domain': kb.domain,
            'exported_at': kb.updated_at.isoformat() if kb.updated_at else '',
            'update_policy': '由界面 DB 维护，保存/导出时自动同步 JSON',
        },
        'companies': {},
        'metrics_aliases': {},
        'report_periods': {},
    }

    # metrics_aliases
    for m in kb.metrics.all():
        data['metrics_aliases'][m.name] = list(m.aliases or [])

    # report_periods
    for p in kb.report_periods.all():
        data['report_periods'][p.name] = list(p.aliases or [])

    # companies + values
    for c in kb.companies.prefetch_related('metric_values', 'metric_values__period', 'metric_values__metric').all():
        entry = {'aliases': list(c.aliases or [])}
        for v in c.metric_values.all():
            period_name = v.period.name
            if period_name not in entry:
                entry[period_name] = {}
            entry[period_name][v.metric.name] = {
                'value': v.value,
                'unit': v.unit or v.metric.default_unit or '',
                'tolerance': v.tolerance if v.tolerance is not None else (v.metric.default_tolerance or 5),
            }
        data['companies'][c.name] = entry

    out_dir = Path(getattr(settings, 'JUDGE_KB_DIR', Path(settings.MEDIA_ROOT) / 'judge_kb'))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / 'financial_kb.json'
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    logger.info(f'[KB] 已导出 KB#{kb.id}({kb.name}) -> {out_path}')
    return out_path


def annotate_kb_stats(queryset):
    """给 KnowledgeBase queryset 标注公司/指标/期间/数值数量。"""
    return queryset.annotate(
        company_count=Count('companies', distinct=True),
        metric_count=Count('metrics', distinct=True),
        period_count=Count('report_periods', distinct=True),
        value_count=Count('companies__metric_values', distinct=True),
    )


# ============================================================
# 文本 → 结构化 KB 解析（支持中文自然语言格式）
# ============================================================
# 常见的数值+单位片段： 1741.4 亿, 862.3亿元, 92 %, 427.2万辆
_NUM_UNIT_RE = re.compile(
    r'(?P<num>-?\d+(?:\.\d+)?)\s*(?P<unit>亿|亿元|亿人民币|万|万元|%|pct|百分点|百分比|万辆|万台|元|美元|港币)?'
)

# 行级分隔（逗号/分号/顿号/换行）
_ROW_SPLIT_RE = re.compile(r'[，,;；、\n]+')


def parse_kb_text(text: str, kb: KnowledgeBase,
                  force_company: str = '', force_period: str = '') -> dict:
    """从文本中解析结构化 KB 项。

    支持格式示例：
      「贵州茅台 2024年报：营业收入 1741.4 亿，归母净利润 862.3 亿，毛利率 92%。」
      「宁德时代 | 2024年报 | 营业收入 3620.1 亿; 归母净利润 441.2 亿; 毛利率 24.4 %」

    返回:
      {
        "companies": [{"name": "贵州茅台", "aliases": []}, ...],
        "periods":   [{"name": "2024年报", "aliases": []}, ...],
        "metrics":   [{"name": "营业收入", "aliases": [], "default_unit": "亿", "default_tolerance": 5}, ...],
        "values":    [{"company": "贵州茅台", "period": "2024年报", "metric": "营业收入", "value": 1741.4, "unit": "亿", "tolerance": 5}, ...],
        "hints":     ["已匹配主体XXX", "新增指标YYY"],
      }
    """
    companies_map: dict[str, dict] = {}
    periods_map: dict[str, dict] = {}
    metrics_map: dict[str, dict] = {}
    values_list: list[dict] = []
    hints: list[str] = []

    # 载入现有 KB 字典（用于匹配已存在的 company/period/metric）
    existing_companies = {c.name: c for c in kb.companies.all()}
    existing_periods = {p.name: p for p in kb.report_periods.all()}
    existing_metrics = {m.name: m for m in kb.metrics.all()}

    def _resolve_company(token: str) -> Optional[str]:
        if force_company:
            return force_company
        token = token.strip()
        if not token:
            return None
        if token in existing_companies:
            return token
        # 别名匹配
        for name, c in existing_companies.items():
            if _matches_alias(token, c.aliases or [], name):
                return name
        return token

    def _resolve_period(token: str) -> Optional[str]:
        if force_period:
            return force_period
        token = token.strip()
        if not token:
            return None
        if token in existing_periods:
            return token
        for name, p in existing_periods.items():
            if _matches_alias(token, p.aliases or [], name):
                return name
        return token

    def _resolve_metric(token: str) -> Optional[str]:
        token = token.strip()
        if not token:
            return None
        if token in existing_metrics:
            return token
        for name, m in existing_metrics.items():
            if _matches_alias(token, m.aliases or [], name):
                return name
        # 不在 KB 里：作为新增指标
        return token

    # ---- 切分大段文本为多条记录 ----
    # 按句点/换行分块，每块尝试作为一条「主体 + 期间 + 若干指标值」记录
    blocks = re.split(r'[。\n]{1,}', text or '')
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # 先尝试分离：主体 期间「:|：| |-」指标1 值1, 指标2 值2
        # 常见分隔符： colon / pipe
        head_body = re.split(r'\s*[:：]\s*', block, maxsplit=1)
        if len(head_body) == 2:
            head, body = head_body
        else:
            head, body = '', block

        # 在 head 中匹配主体和期间
        company_name = force_company
        period_name = force_period
        if head:
            head_tokens = [t for t in re.split(r'[\s|｜\-_]+', head) if t]
            for tok in head_tokens:
                if not company_name:
                    cand = _resolve_company(tok)
                    # 粗略判断：能在现有公司中命中，或长度>=2且含「公司/集团/银行/保险/股份」等关键字，或本身 3+ 个汉字
                    if cand in existing_companies:
                        company_name = cand
                        continue
                    if force_company:
                        company_name = force_company
                        continue
                if not period_name:
                    cand_p = _resolve_period(tok)
                    if cand_p in existing_periods:
                        period_name = cand_p

            # fallback：head 整串匹配公司/期间
            if not company_name:
                c = _resolve_company(head)
                if c in existing_companies:
                    company_name = c
            if not period_name:
                p = _resolve_period(head)
                if p in existing_periods:
                    period_name = p

        # body：切分指标项，每项形如「营业收入 1741.4 亿」
        # 切分小项
        items = _ROW_SPLIT_RE.split(body)
        for item in items:
            item = item.strip()
            if not item:
                continue
            m = _NUM_UNIT_RE.search(item)
            if not m:
                continue
            num = float(m.group('num'))
            unit_raw = m.group('unit') or ''
            unit = _normalize_unit(unit_raw)

            # 指标名：数字之前的部分（去掉首尾符号/空格）
            metric_part = item[: m.start()].strip(' ：:|｜\-_/')
            # 如果还有「：」等取后面
            metric_part = re.split(r'[:：|｜]', metric_part)[-1].strip()
            metric_name = _resolve_metric(metric_part)
            if not metric_name:
                continue

            # 主体/期间兜底：若仍为空，尝试从 item 中找，或用默认
            if not company_name:
                # 用 item 前缀找
                for tok in re.split(r'[\s/|]+', metric_part):
                    c = _resolve_company(tok)
                    if c in existing_companies:
                        company_name = c
                        break
            if not period_name:
                for tok in re.split(r'[\s/|]+', item):
                    p = _resolve_period(tok)
                    if p in existing_periods:
                        period_name = p
                        break

            if not company_name or not period_name or not metric_name:
                hints.append(f'跳过不完整项: {item} (主体={company_name!r}, 期间={period_name!r}, 指标={metric_name!r})')
                continue

            # 写结构化结果
            if company_name not in companies_map:
                ec = existing_companies.get(company_name)
                companies_map[company_name] = {
                    'name': company_name,
                    'aliases': list(ec.aliases) if ec else [],
                }
            if period_name not in periods_map:
                ep = existing_periods.get(period_name)
                periods_map[period_name] = {
                    'name': period_name,
                    'aliases': list(ep.aliases) if ep else [],
                }
            if metric_name not in metrics_map:
                em = existing_metrics.get(metric_name)
                metrics_map[metric_name] = {
                    'name': metric_name,
                    'aliases': list(em.aliases) if em else [],
                    'default_unit': unit or (em.default_unit if em else ''),
                    'default_tolerance': (em.default_tolerance if em else 5.0) or 5.0,
                }
            key = (company_name, period_name, metric_name)
            if any(v['company'] == company_name and v['period'] == period_name and v['metric'] == metric_name for v in values_list):
                hints.append(f'重复项忽略: {key}')
                continue
            em = existing_metrics.get(metric_name)
            tol = (em.default_tolerance if em else 5.0) or 5.0
            values_list.append({
                'company': company_name,
                'period': period_name,
                'metric': metric_name,
                'value': num,
                'unit': unit or (em.default_unit if em else ''),
                'tolerance': tol,
            })

    return {
        'companies': list(companies_map.values()),
        'periods': list(periods_map.values()),
        'metrics': list(metrics_map.values()),
        'values': values_list,
        'hints': hints,
    }


def import_parsed_kb(kb: KnowledgeBase, parsed: dict, user=None) -> dict:
    """将 parse_kb_text 产出的结构化数据写入 DB。
    返回写入统计：{created/updated/skipped, errors}。
    """
    stats = {'company_created': 0, 'company_updated': 0,
             'period_created': 0, 'period_updated': 0,
             'metric_created': 0, 'metric_updated': 0,
             'value_created': 0, 'value_updated': 0,
             'errors': []}

    companies_idx = {}
    for c in parsed.get('companies', []):
        try:
            obj, created = KBCompany.objects.get_or_create(
                kb=kb, name=c['name'],
                defaults={'aliases': c.get('aliases') or [], 'sort_order': 0}
            )
            if not created:
                new_aliases = sorted(set(list(obj.aliases or []) + list(c.get('aliases') or [])))
                if new_aliases != list(obj.aliases or []):
                    obj.aliases = new_aliases
                    obj.save(update_fields=['aliases'])
                    stats['company_updated'] += 1
                else:
                    pass
            else:
                stats['company_created'] += 1
            companies_idx[c['name']] = obj
        except Exception as e:
            stats['errors'].append(f'公司 {c.get("name")}: {e}')

    periods_idx = {}
    for p in parsed.get('periods', []):
        try:
            obj, created = KBReportPeriod.objects.get_or_create(
                kb=kb, name=p['name'],
                defaults={'aliases': p.get('aliases') or [], 'sort_order': 0}
            )
            if not created:
                new_aliases = sorted(set(list(obj.aliases or []) + list(p.get('aliases') or [])))
                if new_aliases != list(obj.aliases or []):
                    obj.aliases = new_aliases
                    obj.save(update_fields=['aliases'])
                    stats['period_updated'] += 1
            else:
                stats['period_created'] += 1
            periods_idx[p['name']] = obj
        except Exception as e:
            stats['errors'].append(f'期间 {p.get("name")}: {e}')

    metrics_idx = {}
    for m in parsed.get('metrics', []):
        try:
            obj, created = KBMetric.objects.get_or_create(
                kb=kb, name=m['name'],
                defaults={
                    'aliases': m.get('aliases') or [],
                    'default_unit': m.get('default_unit') or '',
                    'default_tolerance': float(m.get('default_tolerance') or 5.0),
                    'sort_order': 0,
                }
            )
            if not created:
                changed = False
                new_aliases = sorted(set(list(obj.aliases or []) + list(m.get('aliases') or [])))
                if new_aliases != list(obj.aliases or []):
                    obj.aliases = new_aliases
                    changed = True
                if m.get('default_unit') and obj.default_unit != m['default_unit']:
                    obj.default_unit = m['default_unit']
                    changed = True
                if changed:
                    obj.save(update_fields=['aliases', 'default_unit'])
                    stats['metric_updated'] += 1
            else:
                stats['metric_created'] += 1
            metrics_idx[m['name']] = obj
        except Exception as e:
            stats['errors'].append(f'指标 {m.get("name")}: {e}')

    for v in parsed.get('values', []):
        try:
            company = companies_idx.get(v['company']) or KBCompany.objects.filter(kb=kb, name=v['company']).first()
            period = periods_idx.get(v['period']) or KBReportPeriod.objects.filter(kb=kb, name=v['period']).first()
            metric = metrics_idx.get(v['metric']) or KBMetric.objects.filter(kb=kb, name=v['metric']).first()
            if not (company and period and metric):
                stats['errors'].append(f'数值缺失关联: {v}')
                continue
            obj, created = KBMetricValue.objects.update_or_create(
                company=company, period=period, metric=metric,
                defaults={
                    'value': float(v['value']),
                    'unit': v.get('unit') or metric.default_unit or '',
                    'tolerance': float(v.get('tolerance') if v.get('tolerance') is not None else (metric.default_tolerance or 5.0)),
                    'updated_by': user,
                }
            )
            if created:
                stats['value_created'] += 1
            else:
                stats['value_updated'] += 1
        except Exception as e:
            stats['errors'].append(f'数值 {v}: {e}')

    # 写回 JSON，保证 gt_provider 消费最新
    try:
        export_kb_to_json(kb.id)
    except Exception as e:
        stats['errors'].append(f'导出 JSON 失败: {e}')

    return stats
