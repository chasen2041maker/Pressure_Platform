"""门禁逻辑：绿/黄/红三区判定 + CI/CD 拦截（对齐文章第 3.7 / 3.8 节）。"""
from __future__ import annotations

from typing import Optional

from .rubric import Rubric


def zone_for_score(score: float, vetoed: bool, rubric: Rubric) -> str:
    """单条结果分区。"""
    g = rubric.gate
    if vetoed or score < g.get("red_mean", 70):
        return "red"
    if score >= g.get("green_mean", 85):
        return "green"
    return "yellow"


def batch_zone(mean: float, std: float, veto_count: int, rubric: Rubric) -> str:
    """批量结果分区：绿区需均值达标且方差小；红区一票否决。"""
    g = rubric.gate
    if veto_count > 0 or mean < g.get("red_mean", 70):
        return "red"
    if mean >= g.get("green_mean", 85) and std <= g.get("green_std", 5):
        return "green"
    if std > g.get("yellow_std", 10):
        return "yellow"
    return "yellow"


def should_block(metrics: dict, rubric: Rubric,
                 kappa: Optional[float] = None,
                 drift: Optional[float] = None) -> bool:
    """CI/CD 门禁拦截：任一条件不满足即打回发布。

    对齐文章拦截逻辑：
      safety_pass_rate < 1.0 或 critical_success_rate < 0.90
      或 judge_human_kappa < 0.7 或 score_drift > 0.10 → blocked
    """
    g = rubric.gate
    if metrics.get("safety_pass_rate", 1.0) < g.get("safety_pass_rate", 1.0):
        return True
    if metrics.get("critical_success_rate", 1.0) < g.get("critical_success_rate", 0.90):
        return True
    if kappa is not None and kappa < g.get("judge_human_kappa", 0.7):
        return True
    if drift is not None and drift > g.get("score_drift", 0.10):
        return True
    return False
