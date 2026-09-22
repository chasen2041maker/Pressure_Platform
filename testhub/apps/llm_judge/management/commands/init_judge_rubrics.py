"""将内置 Rubric YAML 模板导入数据库。

用法：
    python manage.py init_judge_rubrics            # 仅导入缺失的模板
    python manage.py init_judge_rubrics --overwrite # 用 YAML 内容覆盖已有同名模板
    python manage.py init_judge_rubrics --default qa # 导入并把指定领域设为默认
"""
from pathlib import Path

import yaml
from django.core.management.base import BaseCommand

from apps.llm_judge.models import Rubric, RubricDimension, RubricRule

TEMPLATES = [
    'finance_rubric_v1.0.yaml',
    'qa_rubric_v1.0.yaml',
    'customer_service_rubric_v1.0.yaml',
]

# filename -> (Rubric.name, domain, description)
META = {
    'finance_rubric_v1.0.yaml': (
        '金融领域评分标准',
        'finance',
        '覆盖数据准确性、时效性、合规性、专业框架、风险提示等 12 个维度，适用于金融投顾问答。',
    ),
    'qa_rubric_v1.0.yaml': (
        '通用问答评分标准',
        'qa',
        '从准确性、完整性、清晰性、相关性、逻辑性 5 个维度评估问答质量，适用于 RAG / 知识问答。',
    ),
    'customer_service_rubric_v1.0.yaml': (
        '客服场景评分标准',
        'customer_service',
        '评估客服回复的准确性、完整性、服务态度、可操作性和信息安全，适用于客服机器人。',
    ),
}

# RubricRule 需要的中文名称映射
RULE_NAMES = {
    'absolute_words': '绝对化用语检测',
    'disclaimer': '免责声明检测',
    'timeliness': '时效性校验',
    'numeric_gt': '数值参考答案校验',
    'custom_regex': '自定义正则',
}


class Command(BaseCommand):
    help = '将内置的金融/通用问答/客服 Rubric 模板导入数据库。'

    def add_arguments(self, parser):
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='若数据库已存在同名 Rubric，用 YAML 内容覆盖其维度和规则。',
        )
        parser.add_argument(
            '--default',
            choices=['finance', 'qa', 'customer_service'],
            help='导入后把指定领域的 Rubric 设为系统默认。',
        )

    def handle(self, *args, **options):
        overwrite = options['overwrite']
        set_default_domain = options.get('default')
        tpl_dir = Path(__file__).resolve().parents[2] / 'judge_engine' / 'rubric_templates'

        created, updated, skipped = 0, 0, 0
        default_rubric = None

        for filename in TEMPLATES:
            path = tpl_dir / filename
            if not path.exists():
                self.stderr.write(self.style.WARNING(f'[跳过] 模板文件不存在: {path}'))
                continue

            with open(path, 'r', encoding='utf-8') as f:
                raw = yaml.safe_load(f)

            name, domain, description = META[filename]
            rubric = Rubric.objects.filter(name=name).first()

            if rubric and not overwrite:
                skipped += 1
                self.stdout.write(f'[跳过] {name} 已存在（使用 --overwrite 可覆盖）')
            else:
                if rubric:
                    # 覆盖：清空旧维度和规则
                    rubric.dimensions.all().delete()
                    rubric.rules.all().delete()
                    rubric.domain = domain
                    rubric.description = description
                    rubric.version = str(raw.get('version', '1.0.0'))
                    rubric.scoring_weights = self._build_scoring(raw)
                    rubric.gate_config = self._build_gate(raw)
                    rubric.save()
                    updated += 1
                    self.stdout.write(self.style.WARNING(f'[更新] {name}'))
                else:
                    rubric = Rubric.objects.create(
                        name=name,
                        domain=domain,
                        description=description,
                        version=str(raw.get('version', '1.0.0')),
                        scoring_weights=self._build_scoring(raw),
                        gate_config=self._build_gate(raw),
                    )
                    created += 1
                    self.stdout.write(self.style.SUCCESS(f'[创建] {name}'))

                # 写入维度（拍平 groups）
                sort_order = 0
                for group in raw.get('groups', []):
                    for dim in group.get('dimensions', []):
                        RubricDimension.objects.create(
                            rubric=rubric,
                            dim_key=dim['id'],
                            name=dim.get('name', dim['id']),
                            dim_type=dim.get('type', 'score'),
                            weight=float(dim.get('weight', 0)),
                            anchor_text={str(k): v for k, v in (dim.get('anchors') or {}).items()},
                            vetoable=bool(dim.get('veto', False)),
                            sort_order=sort_order,
                        )
                        sort_order += 1

                # 写入规则
                self._create_rules(rubric, raw)

            if set_default_domain and rubric.domain == set_default_domain:
                default_rubric = rubric

        if default_rubric:
            Rubric.objects.filter(is_default=True).exclude(pk=default_rubric.pk).update(is_default=False)
            default_rubric.is_default = True
            default_rubric.save(update_fields=['is_default', 'updated_at'])
            self.stdout.write(self.style.SUCCESS(
                f'[默认] 已将 {default_rubric.name} 设为系统默认评分标准'
            ))

        self.stdout.write(self.style.SUCCESS(
            f'\n完成：新建 {created} 套，更新 {updated} 套，跳过 {skipped} 套。'
        ))

    @staticmethod
    def _build_scoring(raw: dict) -> dict:
        scoring = raw.get('scoring') or {}
        return {
            'rule': float(scoring.get('rule', scoring.get('rule_weight', 0.4))),
            'llm': float(scoring.get('llm', scoring.get('llm_weight', 0.6))),
        }

    @staticmethod
    def _build_gate(raw: dict) -> dict:
        gate = dict(raw.get('gate') or {})
        gate.setdefault('green_mean', 85)
        gate.setdefault('yellow_mean', 70)
        gate.setdefault('safety_pass_rate', 1.0)
        gate.setdefault('critical_success_rate', 0.95)
        return gate

    def _create_rules(self, rubric: Rubric, raw: dict):
        sort_order = 0

        # rules.absolute_words / disclaimer
        rules = raw.get('rules') or {}
        for rule_key in ('absolute_words', 'disclaimer'):
            cfg = rules.get(rule_key)
            if not cfg:
                continue
            params = {}
            if rule_key == 'absolute_words':
                if isinstance(cfg, list):
                    params = {'keywords': cfg, 'extra_regex': [], 'check_quoted_context_exempt': True}
                    is_veto = True
                else:
                    params = {
                        'keywords': cfg.get('words', []) or cfg.get('keywords', []),
                        'extra_regex': cfg.get('extra_regex', []),
                        'check_quoted_context_exempt': cfg.get('check_quoted_context_exempt', True),
                    }
                    is_veto = cfg.get('is_veto', True)
            else:  # disclaimer
                params = {
                    'required_keywords': cfg.get('required_keywords', []) or cfg.get('keywords', []),
                    'allowed_patterns': cfg.get('allowed_patterns', []),
                    'exempt_in_citations': cfg.get('exempt_in_citations', True),
                }
                is_veto = cfg.get('is_veto', bool(cfg.get('required_keywords')))

            RubricRule.objects.create(
                rubric=rubric,
                rule_key=rule_key,
                name=RULE_NAMES.get(rule_key, rule_key),
                enabled=True,
                severity='critical' if is_veto else 'warn',
                is_veto=is_veto,
                fallback_mode='keyword',
                params=params,
                sort_order=sort_order,
            )
            sort_order += 1

        # timeliness
        timeliness = raw.get('timeliness') or {}
        if timeliness:
            calendar = timeliness.get('disclosure_calendar') or []
            RubricRule.objects.create(
                rubric=rubric,
                rule_key='timeliness',
                name=RULE_NAMES['timeliness'],
                enabled=True,
                severity='critical' if timeliness.get('veto_on_future_period', True) else 'warn',
                is_veto=timeliness.get('veto_on_future_period', True),
                fallback_mode='keyword',
                params={
                    'market': (calendar[0].get('market') if calendar else 'A股'),
                    'calendar_code': 'CN_A_SHARE',
                    'stale_cycles_threshold': timeliness.get('stale_cycles_threshold', 1),
                    'periods': (calendar[0].get('periods') if calendar else {}),
                },
                sort_order=sort_order,
            )
            sort_order += 1

        # numeric_gt
        numeric_gt = raw.get('numeric_gt') or {}
        if numeric_gt:
            RubricRule.objects.create(
                rubric=rubric,
                rule_key='numeric_gt',
                name=RULE_NAMES['numeric_gt'],
                enabled=True,
                severity='warn',
                is_veto=False,
                fallback_mode='keyword',
                params={
                    'percent_tolerance': numeric_gt.get('percent_tolerance', 0.05),
                    'amount_tolerance_ratio': numeric_gt.get('amount_tolerance_ratio', 0.01),
                    'require_match': numeric_gt.get('require_match', False),
                    'unit_aliases': numeric_gt.get('unit_aliases', {}),
                },
                sort_order=sort_order,
            )
            sort_order += 1

        # custom_regex
        for custom in rules.get('custom_regex', []) or []:
            RubricRule.objects.create(
                rubric=rubric,
                rule_key='custom_regex',
                name=custom.get('name') or RULE_NAMES['custom_regex'],
                enabled=True,
                severity=custom.get('severity', 'warn'),
                is_veto=custom.get('is_veto', False),
                fallback_mode='regex',
                params={
                    'pattern': custom.get('pattern', ''),
                    'message_template': custom.get('message_template', '命中 {matched}'),
                    'group': custom.get('group', 0),
                },
                sort_order=sort_order,
            )
            sort_order += 1
