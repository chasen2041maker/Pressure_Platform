"""Pydantic 数据模型：评分引擎内部传输对象（与 Django ORM models 分离）。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Label = Literal["excellent", "acceptable", "needs_improvement", "critical_failure"]
Severity = Literal["info", "warn", "critical"]
Zone = Literal["green", "yellow", "red"]


# ---------- 规则引擎 ----------

class RuleFinding(BaseModel):
    rule: str
    severity: Severity
    message: str
    detail: dict = Field(default_factory=dict)


class RuleReport(BaseModel):
    findings: list[RuleFinding] = Field(default_factory=list)
    rule_score: float = 100.0
    vetoed: bool = False
    veto_reasons: list[str] = Field(default_factory=list)


# ---------- LLM Judge ----------

class DimensionScore(BaseModel):
    id: str
    score: Optional[float] = None
    passed: Optional[bool] = None
    reasoning: str = ""


class JudgeVerdict(BaseModel):
    reasoning: str = ""
    dimensions: list[DimensionScore] = Field(default_factory=list)
    overall_label: Label = "acceptable"
    model: str = ""


# ---------- 评分请求 / 响应 ----------

class GroundTruth(BaseModel):
    text: Optional[str] = None
    values: list[dict] = Field(default_factory=list)


class ScoreRequest(BaseModel):
    question: str
    answer: str
    ground_truth: Optional[GroundTruth] = None
    auto_gt: bool = False
    context: dict = Field(default_factory=dict)
    rubric_id: Optional[int] = None


class ScoreResponse(BaseModel):
    request_id: str
    rule_report: RuleReport
    verdict: JudgeVerdict
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    llm_score: float = 0.0
    final_score: float = 0.0
    overall_label: Label = "needs_improvement"
    gate_zone: Zone = "red"
    meta: dict = Field(default_factory=dict)
