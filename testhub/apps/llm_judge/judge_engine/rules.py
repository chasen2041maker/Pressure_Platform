"""规则引擎：确定性检查先行，LLM 只评开放维度。

覆盖四类规则（对应 YAML 中 rules / timeliness / numeric_gt 配置）：
1. 空输出 / 错误话术检测
2. 绝对化词检测（合规一票否决）
3. 免责声明缺失检测（构成投资建议时必须带免责声明）
4. 时效性预检（解析报告期 vs 披露日历：引用未披露报告期 → 否决；数据过时 → 告警）
5. 数值参考答案校验（带容差与单位匹配）
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

from .models import GroundTruth, RuleFinding, RuleReport, ScoreRequest
from .rubric import Rubric

SEV_PENALTY = {"info": 0, "warn": 10, "critical": 35}

PERIOD_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(20\d{2})\s*年\s*(?:一季报|一季度)"), "Q1"),
    (re.compile(r"(20\d{2})\s*年\s*(?:中报|半年报|二季报|二季度)"), "H1"),
    (re.compile(r"(20\d{2})\s*年\s*(?:三季报|三季度)"), "Q3"),
    (re.compile(r"(20\d{2})\s*年\s*(?:年报|年度报告)"), "FY"),
    (re.compile(r"(20\d{2})\s*[Qq]([1-4])"), None),  # 动态映射
]
Q_MAP = {"1": "Q1", "2": "H1", "3": "Q3", "4": "FY"}

NUM_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*(万亿|亿|万|%|％)?")


def _as_date(d) -> Optional[date]:
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        try:
            return datetime.strptime(d[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def extract_periods(text: str) -> list[tuple[int, str]]:
    """解析答案中的报告期表述，返回 [(year, code)]，code ∈ {Q1, H1, Q3, FY}。"""
    out = []
    for pat, code in PERIOD_PATTERNS:
        for m in pat.finditer(text):
            year = int(m.group(1))
            if code is None:
                code = Q_MAP.get(m.group(2))
            out.append((year, code))
    return out


class RuleEngine:
    def __init__(self, rubric: Rubric):
        self.rubric = rubric
        self.rules = rubric.rules
        self.calendar = self._load_calendar()

    def _load_calendar(self) -> dict:
        markets = self.rubric.timeliness.get("disclosure_calendar", [])
        return markets[0] if markets else {"periods": {}}

    # ---------- 规则检查 ----------

    def _check_empty(self, answer: str) -> list[RuleFinding]:
        text = (answer or "").strip()
        phrases = self.rules.get("empty_phrases", [])
        if not text:
            return [RuleFinding(rule="empty", severity="critical", message="答案为空")]
        hit = [p for p in phrases if p in text]
        if hit:
            return [RuleFinding(rule="empty", severity="critical",
                                message=f"答案包含错误话术: {'/'.join(hit)}")]
        return []

    def _check_absolute_words(self, answer: str) -> list[RuleFinding]:
        words = self.rules.get("absolute_words", [])
        hit = [w for w in words if w in (answer or "")]
        if hit:
            return [RuleFinding(
                rule="absolute_words",
                severity="critical",
                message=f"检测到绝对化/承诺性表述: {'/'.join(hit)}（合规一票否决）",
                detail={"words": hit},
            )]
        # 关键词未命中 → LLM 兜底（仅疑似模式触发，可配置关闭）
        from .rules_llm import llm_check_absolute_words
        llm_finding = llm_check_absolute_words(answer or "")
        if llm_finding:
            return [llm_finding]
        return []

    def _check_disclaimer(self, question: str, answer: str) -> list[RuleFinding]:
        if not self.rules.get("require_disclaimer", True):
            return []
        rec_words = self.rules.get("recommendation_words", [])
        disc_words = self.rules.get("disclaimer_keywords", [])
        is_recommendation = any(w in (answer or "") for w in rec_words)
        has_disclaimer = any(w in (answer or "") for w in disc_words)
        if not is_recommendation or has_disclaimer:
            return []
        # 关键词判定：构成建议但未匹配到免责声明
        # LLM 兜底：确认是否真有变形免责声明（LLM 判定有免责 → 不否决）
        from .rules_llm import is_fallback_enabled, llm_check_disclaimer
        if is_fallback_enabled():
            llm_finding = llm_check_disclaimer(question, answer or "")
            if llm_finding:
                return [llm_finding]
            # LLM 已成功调用且判定无问题（有变形免责或非投资建议）→ 不否决
            return []
        # LLM 未启用 → 降级为关键词匹配结果（一票否决）
        return [RuleFinding(
            rule="disclaimer",
            severity="critical",
            message="答案构成投资建议但缺少免责声明（如'不构成投资建议'）（合规一票否决）",
        )]

    def _deadline(self, year: int, code: str) -> Optional[date]:
        p = self.calendar.get("periods", {}).get(code)
        if not p:
            return None
        y = year + 1 if p.get("next_year") else year
        return date(y, p["deadline_month"], p["deadline_day"])

    def _check_timeliness(self, answer: str, today: date) -> list[RuleFinding]:
        periods = extract_periods(answer or "")
        findings: list[RuleFinding] = []
        if not periods:
            return [RuleFinding(
                rule="timeliness", severity="info",
                message="未检测到明确报告期表述，时效性交由 LLM 判断",
            )]

        # 1) 引用尚未披露的报告期 → 一票否决（疑似编造）
        future = []
        for year, code in periods:
            dl = self._deadline(year, code)
            if dl and dl > today:
                future.append(f"{year}{code}(应于 {dl} 前披露)")
        if future and self.rubric.timeliness.get("veto_on_future_period", True):
            findings.append(RuleFinding(
                rule="timeliness", severity="critical",
                message=f"引用了尚未披露的报告期数据: {'/'.join(future)}（疑似编造，一票否决）",
                detail={"future_periods": future},
            ))

        # 2) 数据过时告警：最新引用周期落后于当前最新可得超过阈值
        latest, latest_idx = self._latest_available(today)
        cited_idx = max(
            (
                self._period_index(y, c)
                for y, c in periods
                if (self._deadline(y, c) or date.max) <= today
            ),
            default=None,
        )
        stale_th = self.rubric.timeliness.get("stale_cycles_threshold", 1)
        if latest and cited_idx is not None and latest_idx - cited_idx > stale_th:
            label = f"{latest[0]}{latest[1]}"
            findings.append(RuleFinding(
                rule="timeliness", severity="warn",
                message=f"数据可能过时：当前最新可得报告期为 {label}，答案仅引用更早数据",
            ))
        return findings

    def _period_index(self, year: int, code: str) -> int:
        """报告期在时间轴上的序号（FY(y-1) < Q1(y) < H1(y) < Q3(y) < FY(y)）。"""
        order = {"Q1": 1, "H1": 2, "Q3": 3, "FY": 4}
        return year * 4 + order.get(code, 0)

    def _latest_available(self, today: date) -> tuple[Optional[tuple[int, str]], int]:
        candidates = []
        for y in (today.year - 1, today.year):
            for code in ("Q1", "H1", "Q3", "FY"):
                dl = self._deadline(y, code)
                if dl and dl <= today:
                    candidates.append((y, code, dl))
        if not candidates:
            return None, 0
        best = max(candidates, key=lambda c: (c[2], self._period_index(c[0], c[1])))
        return (best[0], best[1]), self._period_index(best[0], best[1])

    def _check_numeric_gt(self, answer: str, gt: Optional[GroundTruth]) -> list[RuleFinding]:
        if not gt or not gt.values:
            return []
        findings: list[RuleFinding] = []
        cfg = self.rubric.numeric_gt
        pct_tol = cfg.get("percent_tolerance", 0.05)
        ratio_tol = cfg.get("amount_tolerance_ratio", 0.01)
        tokens = [(float(m.group(1)), m.group(2)) for m in NUM_RE.finditer(answer or "")]
        for item in gt.values:
            label = item.get("label", "参考答案")
            value = float(item["value"])
            unit = item.get("unit") or ""
            tol = item.get("tolerance")
            if tol is None:
                tol = pct_tol if unit in ("%", "％") else ratio_tol * abs(value)
            best = None
            for n, u in tokens:
                if u != unit:
                    continue
                if abs(n - value) <= tol:
                    best = n
                    break
            if best is not None:
                findings.append(RuleFinding(
                    rule="numeric_gt", severity="info",
                    message=f"数值校验通过: {label}={value}{unit}（答案中出现 {best}{unit}）",
                ))
            else:
                sev = "critical" if cfg.get("require_match", False) else "warn"
                findings.append(RuleFinding(
                    rule="numeric_gt", severity=sev,
                    message=f"未在答案中找到与参考答案一致的数值: {label}={value}{unit}（容差 {tol}）",
                    detail={"expected": value, "unit": unit, "tolerance": tol},
                ))
        return findings

    # ---------- 汇总 ----------

    def run(self, req: ScoreRequest) -> RuleReport:
        today = _as_date((req.context or {}).get("as_of_date")) or date.today()
        findings: list[RuleFinding] = []
        findings += self._check_empty(req.answer)
        findings += self._check_absolute_words(req.answer)
        findings += self._check_disclaimer(req.question, req.answer)
        findings += self._check_timeliness(req.answer, today)
        findings += self._check_numeric_gt(req.answer, req.ground_truth)

        veto_reasons = [f.message for f in findings if f.severity == "critical"]
        vetoed = bool(veto_reasons)

        score = 100.0
        for f in findings:
            score -= SEV_PENALTY.get(f.severity, 0)
        score = max(0.0, min(100.0, score))
        if vetoed:
            score = 0.0
        return RuleReport(findings=findings, rule_score=score,
                          vetoed=vetoed, veto_reasons=veto_reasons)
