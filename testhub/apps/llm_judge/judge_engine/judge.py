"""Judge 执行引擎：CoT 强制推理 + temperature=0 + 结构化 JSON 输出。

对齐文章关键设计：
- 先输出详细评判理由（reasoning），再给分数（直接打分一致性低 25-40%）
- 零温度排除随机性；同一答案跑 n_runs 次取中位数
- 支持多裁判投票（judge_models 传多个模型名，取中位数降单模型偏见）
- 无 OPENAI_API_KEY 时可用 MockJudge 离线联调
"""
from __future__ import annotations

import json
import os
import statistics
from typing import Optional

from .models import DimensionScore, JudgeVerdict, ScoreRequest
from .rubric import Rubric
from .config import get_config

LABELS = ["excellent", "acceptable", "needs_improvement", "critical_failure"]


class JudgeEngine:
    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None,
                 base_url: Optional[str] = None,
                 n_runs: int = 3, judge_models: Optional[list[str]] = None):
        cfg = get_config()
        self.model = model or cfg.judge_model
        self.api_key = api_key or cfg.openai_api_key
        self.base_url = base_url or cfg.openai_base_url or None
        self.n_runs = max(1, n_runs)
        self.judge_models = judge_models or [self.model]
        self._client = None

    def _client_lazy(self):
        if self._client is None:
            from openai import OpenAI
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    # ---------- Prompt 构造 ----------

    def build_prompt(self, req: ScoreRequest, rubric: Rubric,
                     findings: list) -> list[dict]:
        hint_lines = [f"[规则引擎提示 {f.severity}] {f.message}" for f in findings]
        gt = req.ground_truth
        gt_text = (gt.text if gt else None) or ""
        values_text = ""
        if gt and gt.values:
            values_text = "；".join(
                f"{v.get('label', '指标')}={v['value']}{v.get('unit', '')}" for v in gt.values
            )
        domain_label = rubric.domain_label()
        rubric_desc = rubric.description or '无'
        system = (
            f"你是{domain_label}领域的答案质量评审专家（Judge）。"
            f"本次评审采用评分标准【{rubric.name}】（领域：{domain_label}，"
            f"版本：{rubric.version or '1.0'}；说明：{rubric_desc}）。"
            "你只能依据给定的评分标准与参考答案客观评分，不臆测、不偏袒。"
            "必须先用 CoT 逐维度说明评判理由，再输出结构化 JSON。"
        )
        user = f"""# 评分任务

## 用户问题
{req.question}

## 待评答案
{req.answer}

## 参考答案（可能为空）
{gt_text or '（无参考答案）'}
{('数值参考答案：' + values_text) if values_text else ''}

## 规则引擎预检结果
{' '.join(hint_lines) if hint_lines else '（无特殊提示）'}

## 评分标准（{rubric.name} | {domain_label}）
### 评分维度
{rubric.score_anchors_text()}
### 一票否决项
{rubric.veto_text()}

## 输出要求
1. 先输出 reasoning：逐维度说明打分依据（引用答案原文与事实核对过程）。
2. 再按 JSON Schema 输出：
{{"reasoning": str, "dimensions": [{{"id": str, "score": int(1-5), "reasoning": str}}], "overall_label": "excellent|acceptable|needs_improvement|critical_failure"}}
3. score 型维度必须给出 1-5 整数分；整体评级结合最短板与平均水平综合判断。
4. 只输出 JSON，不要输出额外文字。"""
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    # ---------- 调用与解析 ----------

    def _call(self, messages: list[dict], model: str) -> dict:
        client = self._client_lazy()
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        return json.loads(content)

    def _parse(self, data: dict, model: str) -> JudgeVerdict:
        dims = []
        for item in data.get("dimensions", []):
            d = DimensionScore(
                id=str(item.get("id", "")),
                score=item.get("score"),
                passed=item.get("passed"),
                reasoning=str(item.get("reasoning", "")),
            )
            dims.append(d)
        label = data.get("overall_label", "acceptable")
        if label not in LABELS:
            label = "acceptable"
        return JudgeVerdict(
            reasoning=str(data.get("reasoning", "")),
            dimensions=dims,
            overall_label=label,
            model=model,
        )

    def judge(self, req: ScoreRequest, rubric: Rubric, findings: list) -> JudgeVerdict:
        # 缓存由 JudgeService 层统一管理（build_cache_key + get_cached/set_cached），
        # 此方法只负责 LLM 调用 + 多次运行聚合
        messages = self.build_prompt(req, rubric, findings)
        verdicts = []
        for model in self.judge_models:
            for _ in range(self.n_runs):
                try:
                    verdicts.append(self._parse(self._call(messages, model), model))
                except (json.JSONDecodeError, KeyError, ValueError):
                    # 解析失败重试一次，仍失败则降级为中性分
                    try:
                        verdicts.append(self._parse(self._call(messages, model), model))
                    except Exception:
                        verdicts.append(self._neutral_verdict(model))
        return self._aggregate(verdicts)

    def _neutral_verdict(self, model: str) -> JudgeVerdict:
        return JudgeVerdict(reasoning="Judge 解析失败，使用中性分兜底", model=model)

    def _aggregate(self, verdicts: list[JudgeVerdict]) -> JudgeVerdict:
        if not verdicts:
            return self._neutral_verdict("none")
        dim_ids = sorted({d.id for v in verdicts for d in v.dimensions})
        dims = []
        for did in dim_ids:
            scores = [
                d.score for v in verdicts for d in v.dimensions
                if d.id == did and d.score is not None
            ]
            passes = [
                d.passed for v in verdicts for d in v.dimensions
                if d.id == did and d.passed is not None
            ]
            dims.append(DimensionScore(
                id=did,
                score=round(statistics.median(scores), 1) if scores else None,
                passed=(passes and all(passes)) if passes else None,
                reasoning="",
            ))
        label_counts = {}
        for v in verdicts:
            label_counts[v.overall_label] = label_counts.get(v.overall_label, 0) + 1
        label = max(label_counts, key=label_counts.get)
        return JudgeVerdict(
            reasoning=verdicts[0].reasoning,
            dimensions=dims,
            overall_label=label,
            model="+".join(sorted({v.model for v in verdicts})),
        )


    def test_connection(self) -> tuple[bool, str]:
        """测试 LLM 连通性（给配置页用）。"""
        try:
            client = self._client_lazy()
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                temperature=0,
            )
            return True, f"连通成功: model={self.model}"
        except Exception as e:
            return False, f"连通失败: {e}"


class MockJudge:
    """离线联调用确定性 Judge：critical 触发一票否决评分，warn 逐条扣分。"""

    def __init__(self):
        self.model = "mock-judge"

    def test_connection(self) -> tuple[bool, str]:
        return True, "MockJudge 模式，无需连通测试"

    def judge(self, req: ScoreRequest, rubric: Rubric, findings: list) -> JudgeVerdict:
        criticals = [f for f in findings if f.severity == "critical"]
        warns = [f for f in findings if f.severity == "warn"]
        if criticals:
            dims = [DimensionScore(id=d.id, score=1.0, reasoning="规则否决") for d in rubric.score_dims]
            return JudgeVerdict(
                reasoning="; ".join(f.message for f in criticals),
                dimensions=dims, overall_label="critical_failure", model=self.model,
            )
        base = 4.0 - min(1.5, 0.5 * len(warns))
        dims = [DimensionScore(id=d.id, score=max(1.0, base), reasoning="mock") for d in rubric.score_dims]
        mean = base
        if mean >= 4.5:
            label = "excellent"
        elif mean >= 3.5:
            label = "acceptable"
        else:
            label = "needs_improvement"
        return JudgeVerdict(
            reasoning="mock judge: " + "; ".join(f.message for f in warns) if warns else "mock judge: 无规则告警",
            dimensions=dims, overall_label=label, model=self.model,
        )
