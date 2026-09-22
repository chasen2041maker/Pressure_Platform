"""JudgeService：评分引擎对外统一入口。

被 Django View（同步）和 Celery Task（异步批量）共同调用。
职责：
1. 加载 Rubric（DB → YAML fallback）
2. auto_gt 自动匹配 ground_truth
3. 规则引擎预检
4. 缓存检查（Django cache）
5. LLM Judge（cache miss 时，n_runs 取中位数）
6. 合成最终分 + 门禁分区
7. 落库 JudgeRecord
"""
from __future__ import annotations

import hashlib
import logging
import statistics
import time
from typing import Any, Optional

from .cache import build_cache_key, get_cached, set_cached
from .config import get_config
from .gateway import zone_for_score
from .gt_provider import auto_match as gt_auto_match
from .judge import JudgeEngine, MockJudge
from .models import GroundTruth, ScoreRequest
from .rubric import Rubric
from .rubric_loader import load_rubric
from .rules import RuleEngine
from .scorer import build_response

logger = logging.getLogger(__name__)


class JudgeService:
    """评分服务（无状态，可被 View/Celery 反复实例化）。"""

    def __init__(self, rubric_id: Optional[int] = None):
        self.rubric_id = rubric_id
        self._rubric: Optional[Rubric] = None
        self._rubric_orm_id: Optional[int] = None
        self._rules: Optional[RuleEngine] = None
        self._judge = None

    # ---------- 懒加载 ----------

    @property
    def rubric(self) -> Rubric:
        if self._rubric is None:
            self._rubric, self._rubric_orm_id = load_rubric(rubric_id=self.rubric_id)
        return self._rubric

    @property
    def rules_engine(self) -> RuleEngine:
        if self._rules is None:
            self._rules = RuleEngine(self.rubric)
        return self._rules

    @property
    def judge_engine(self):
        if self._judge is None:
            cfg = get_config()
            if cfg.judge_mock:
                self._judge = MockJudge()
            else:
                self._judge = JudgeEngine(
                    model=cfg.judge_model,
                    api_key=cfg.openai_api_key,
                    base_url=cfg.openai_base_url,
                    n_runs=cfg.n_runs,
                )
        return self._judge

    # ---------- 核心评分 ----------

    def score_single(self, req_data: dict, batch=None, created_by=None) -> dict:
        """单条评分（完整流程）。

        Args:
            req_data: {question, answer, ground_truth?, auto_gt?, context?, rubric?}
            batch: JudgeBatch ORM 实例（批量评分时传入）
            created_by: User 实例

        Returns:
            评分结果 dict（与 ScoreResponse 对齐 + request_id + cache_hit）
        """
        t0 = time.time()

        # 1. 构造 ScoreRequest
        rubric_id = req_data.get('rubric') or self.rubric_id
        if rubric_id and rubric_id != self.rubric_id:
            self.rubric_id = rubric_id
            self._rubric = None
            self._rules = None

        gt_raw = req_data.get('ground_truth')
        ground_truth = GroundTruth(**gt_raw) if gt_raw else None

        req = ScoreRequest(
            question=req_data['question'],
            answer=req_data['answer'],
            ground_truth=ground_truth,
            auto_gt=req_data.get('auto_gt', False),
            context=req_data.get('context', {}),
        )

        # 2. auto_gt 匹配
        if req.auto_gt and not req.ground_truth:
            try:
                req.ground_truth = gt_auto_match(req.question, req.context)
            except Exception as e:
                logger.warning(f'[JudgeService] auto_gt 失败: {e}')

        # 3. 规则引擎预检
        rule_report = self.rules_engine.run(req)

        # 4. 缓存检查
        cfg = get_config()
        gt_values = req.ground_truth.values if req.ground_truth else []
        cache_key = build_cache_key(
            req.question, req.answer, gt_values,
            str(self.rubric.version), cfg.judge_model,
        )
        cached_verdict = get_cached(cache_key)
        cache_hit = cached_verdict is not None

        # 5. LLM Judge（或缓存命中）
        if cache_hit:
            verdict = _dict_to_verdict(cached_verdict)
        else:
            try:
                verdict = self.judge_engine.judge(req, self.rubric, rule_report.findings)
            except Exception as exc:
                logger.error(f'[JudgeService] LLM Judge 失败: {exc}')
                raise RuntimeError(f'Judge 调用失败: {exc}') from exc
            # 写缓存
            set_cached(cache_key, _verdict_to_dict(verdict), timeout=cfg.cache_timeout)

        # 6. 合成最终分
        request_id = _gen_request_id(req.question, req.answer)
        response = build_response(
            self.rubric, req, rule_report, verdict, request_id=request_id,
        )

        latency_ms = int((time.time() - t0) * 1000)

        # 7. 落库 JudgeRecord
        record = self._save_record(
            req, response, verdict, rule_report,
            request_id, latency_ms, cache_hit, batch, created_by,
        )

        # 8. 返回 dict（给 View/前端用）
        return _response_to_dict(response, record, latency_ms=latency_ms, cache_hit=cache_hit)

    def _save_record(self, req, response, verdict, rule_report,
                     request_id, latency_ms, cache_hit, batch, created_by):
        """落库 JudgeRecord。"""
        try:
            from apps.llm_judge.models import JudgeRecord
            record = JudgeRecord.objects.create(
                request_id=request_id,
                rubric_id=self._rubric_orm_id,
                batch=batch,
                question=req.question,
                answer=req.answer,
                ground_truth=req.ground_truth.model_dump() if req.ground_truth else None,
                context=req.context,
                auto_gt=req.auto_gt,
                rule_score=response.rule_report.rule_score,
                llm_score=response.llm_score,
                final_score=response.final_score,
                overall_label=response.overall_label,
                gate_zone=response.gate_zone,
                blocked=(response.gate_zone == 'red'),
                rule_findings=[f.model_dump() for f in rule_report.findings],
                vetoed=rule_report.vetoed,
                veto_reasons=rule_report.veto_reasons,
                verdict_reasoning=verdict.reasoning,
                verdict_dimensions=[d.model_dump() for d in verdict.dimensions],
                judge_model=verdict.model,
                latency_ms=latency_ms,
                cache_hit=cache_hit,
                created_by=created_by,
            )
            return record
        except Exception as e:
            logger.warning(f'[JudgeService] JudgeRecord 落库失败: {e}')
            return None

    # ---------- 批量汇总 ----------

    def summarize_batch(self, results: list) -> dict:
        """汇总批量评分结果。"""
        valid = [r for r in results if 'error' not in r]
        if not valid:
            return {
                'mean_score': 0.0, 'std_dev': 0.0,
                'safety_pass_rate': 0.0, 'critical_success_rate': 0.0,
                'gate_zone': 'red', 'blocked': True,
            }

        scores = [r.get('final_score', 0) for r in valid]
        mean = statistics.fmean(scores)
        std = statistics.pstdev(scores) if len(scores) > 1 else 0.0
        veto_count = sum(1 for r in valid if r.get('vetoed', False))
        safety_pass_rate = 1.0 - veto_count / len(valid)
        critical_success_rate = sum(
            1 for r in valid if r.get('overall_label') != 'critical_failure'
        ) / len(valid)

        # 门禁分区（与 gateway.zone_for_score 逻辑对齐）
        cfg = get_config()
        gate = self.rubric.gate or {}
        green_mean = gate.get('green_mean', 85)
        yellow_mean = gate.get('yellow_mean', 70)
        required_safety = gate.get('safety_pass_rate', 1.0)
        required_critical = gate.get('critical_success_rate', 0.95)

        if mean >= green_mean and safety_pass_rate >= required_safety and critical_success_rate >= required_critical:
            zone = 'green'
        elif mean >= yellow_mean:
            zone = 'yellow'
        else:
            zone = 'red'

        return {
            'mean_score': round(mean, 2),
            'std_dev': round(std, 2),
            'safety_pass_rate': round(safety_pass_rate, 4),
            'critical_success_rate': round(critical_success_rate, 4),
            'gate_zone': zone,
            'blocked': zone == 'red',
        }


# ---------- 辅助函数 ----------

def _gen_request_id(question: str, answer: str) -> str:
    import time as _t, random as _r
    raw = f'{question[:50]}|{answer[:50]}'
    base = f's{abs(hash(raw)) % 10**8}'
    tail = f'{int(_t.time()*1000)%10000:04d}{_r.randint(0,9999):04d}'
    return f'{base}{tail}'


def _verdict_to_dict(verdict) -> dict:
    return {
        'reasoning': verdict.reasoning,
        'dimensions': [d.model_dump() for d in verdict.dimensions],
        'overall_label': verdict.overall_label,
        'model': verdict.model,
    }


def _dict_to_verdict(d: dict):
    from .models import DimensionScore, JudgeVerdict
    return JudgeVerdict(
        reasoning=d.get('reasoning', ''),
        dimensions=[DimensionScore(**dd) for dd in d.get('dimensions', [])],
        overall_label=d.get('overall_label', 'acceptable'),
        model=d.get('model', ''),
    )


def _response_to_dict(response, record=None, latency_ms: int = 0, cache_hit: bool = False) -> dict:
    """ScoreResponse → dict（给 DRF Response 用）。

    Args:
        response: ScoreResponse
        record: JudgeRecord or None
        latency_ms: 本次评分总耗时（毫秒），用于前端展示
        cache_hit: LLM verdict 是否命中缓存
    """
    # record 落库后可能带回 db 层的最新字段（latency_ms/cache_hit），以 record 为准兜底
    rec_latency = getattr(record, 'latency_ms', None)
    rec_cache = getattr(record, 'cache_hit', None)
    final_latency = rec_latency if rec_latency is not None else latency_ms
    final_cache = rec_cache if rec_cache is not None else cache_hit
    return {
        'request_id': response.request_id,
        'rule_report': response.rule_report.model_dump(),
        'verdict': response.verdict.model_dump(),
        'dimension_scores': response.dimension_scores,
        'llm_score': response.llm_score,
        'final_score': response.final_score,
        'overall_label': response.overall_label,
        'gate_zone': response.gate_zone,
        'meta': {
            **(response.meta or {}),
            'latency_ms': final_latency,
            'cache_hit': bool(final_cache),
        },
        'rule_score': response.rule_report.rule_score,
        'vetoed': response.rule_report.vetoed,
        'veto_reasons': response.rule_report.veto_reasons,
        'rule_findings': [f.model_dump() for f in response.rule_report.findings],
        'judge_model': response.verdict.model,
        'latency_ms': final_latency,
        'cache_hit': bool(final_cache),
        'record_id': record.id if record else None,
    }
