"""评分合成：规则分 × 0.4 + LLM 语义分 × 0.6，一票否决直接归零。"""
from __future__ import annotations

import uuid

from .gateway import zone_for_score
from .models import JudgeVerdict, RuleReport, ScoreRequest, ScoreResponse
from .rubric import Rubric


def compute_llm_score(rubric: Rubric, verdict: JudgeVerdict) -> float:
    """按维度权重加权：Σ(weight × score) / Σ(weight) / 5 × 100。"""
    acc, total_w = 0.0, 0.0
    for ds in verdict.dimensions:
        d = rubric.dim_map.get(ds.id)
        if not d or d.type != "score" or ds.score is None:
            continue
        acc += d.weight * float(ds.score)
        total_w += d.weight
    if total_w == 0:
        return 0.0
    return acc / total_w / 5.0 * 100.0


def build_response(rubric: Rubric, req: ScoreRequest,
                   rule_report: RuleReport, verdict: JudgeVerdict,
                   request_id: str | None = None, meta: dict | None = None) -> ScoreResponse:
    scoring = rubric.scoring
    rw = float(scoring.get("rule_weight", 0.4))
    lw = float(scoring.get("llm_weight", 0.6))

    llm_score = compute_llm_score(rubric, verdict)
    vetoed = rule_report.vetoed

    if vetoed:
        final_score = 0.0
        label = "critical_failure"
    else:
        final_score = round(rw * rule_report.rule_score + lw * llm_score, 1)
        label = verdict.overall_label

    dim_scores = {}
    for ds in verdict.dimensions:
        d = rubric.dim_map.get(ds.id)
        if d and d.type == "score" and ds.score is not None:
            dim_scores[ds.id] = round(float(ds.score), 1)

    return ScoreResponse(
        request_id=request_id or uuid.uuid4().hex[:12],
        rule_report=rule_report,
        verdict=verdict,
        dimension_scores=dim_scores,
        llm_score=round(llm_score, 1),
        final_score=final_score,
        overall_label=label,
        gate_zone=zone_for_score(final_score, vetoed, rubric),
        meta={
            **({"rule_weight": rw, "llm_weight": lw} if meta is None else meta),
            "veto_reasons": rule_report.veto_reasons,
        },
    )
