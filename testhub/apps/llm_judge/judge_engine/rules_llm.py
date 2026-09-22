"""规则引擎 LLM 兜底：关键词匹配漏判时的语义检测。

适用场景：
  · 绝对化词变种："必涨↑""稳赢""只涨不跌""包盈利"（不在硬编码列表里）
  · 免责声明变形："以上不构成建议""此文不含投资建议"（关键词未覆盖）

设计原则：
  1. 关键词匹配先行（毫秒级、零成本），命中直接返回
  2. 仅在关键词未命中且答案疑似有风险时才调 LLM（低成本兜底）
  3. LLM 兜底失败时降级为"未检出"（不阻断主流程，宁可漏判不误判）
  4. 可通过环境变量关闭（RULE_LLM_FALLBACK=0）

配置（环境变量）：
  RULE_LLM_FALLBACK   =1 启用（默认关闭，避免离线/测试场景误调 LLM）
  RULE_LLM_MODEL      兜底用的模型（默认复用 JUDGE_MODEL）
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from .models import RuleFinding
from .config import get_config


# ---------- 启用开关 ----------
_cfg = get_config()
_FALLBACK_ENABLED = _cfg.rule_llm_fallback
_FALLBACK_MODEL = _cfg.judge_model


def is_fallback_enabled() -> bool:
    """供 rules.py 判断是否启用了 LLM 兜底（决定降级行为）。"""
    return _FALLBACK_ENABLED

# ---------- 疑似风险正则（触发 LLM 兜底的前置条件） ----------
# 绝对化词的疑似模式：包含"涨/跌/赚/亏/盈/利"等收益相关词 + 强语气词
_SUSPICIOUS_ABSOLUTE = re.compile(
    r"(?:必|稳|包|绝对|肯定|一定|铁定|准|稳稳|百分百|百分之百|100%|万无一失)"
    r".*?(?:涨|赚|盈|利|赢|赚|不亏|不赔|不跌|收益)"
    r"|(?:涨|赚|盈|利|赢|不亏|不赔).{0,4}(?:↑|↑|➚|📈)"
)
# 免责声明的疑似模式：包含"建议/不构成/仅供参考"的变形表述
_SUSPICIOUS_DISCLAIMER_ABSENT = re.compile(
    r"(?:建议|强烈|值得|推荐).{0,6}(?:买入|卖出|持有|加仓|减仓|配置|关注)"
)


def _call_llm(prompt: str, system: str) -> Optional[dict]:
    """调用 LLM 返回 JSON。失败返回 None。"""
    try:
        from openai import OpenAI
        api_key = get_config().openai_api_key
        if not api_key:
            return None
        kwargs = {"api_key": api_key}
        base_url = get_config().openai_base_url
        if base_url:
            kwargs["base_url"] = base_url
        client = OpenAI(**kwargs)
        resp = client.chat.completions.create(
            model=_FALLBACK_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception:  # noqa: BLE001
        return None


# ---------- 绝对化词 LLM 兜底 ----------

_ABSOLUTE_SYSTEM = (
    "你是金融合规审核员。判断答案中是否包含绝对化、承诺性、保证收益类的违规表述。"
    "绝对化表述包括但不限于：承诺涨跌（必涨/稳赚/包赚/肯定涨）、"
    "保证收益（无风险/保本/稳赚不赔/只赚不亏/100%收益）、"
    "变形表述（必涨↑/稳赢/包盈利/铁定涨/稳稳赚）。"
    "只输出 JSON：{\"hit\": bool, \"words\": [命中的具体表述]}"
)

_ABSOLUTE_USER_TMPL = """判断以下答案是否包含绝对化/承诺性表述：

答案：
{answer}

判断要求：
1. 仅关注对收益/涨跌的绝对承诺，正常的"预计增长""有望提升"不算
2. 返回命中的具体表述原文（如有）
3. 只输出 JSON"""


def llm_check_absolute_words(answer: str) -> Optional[RuleFinding]:
    """LLM 兜底检测绝对化词。仅在疑似模式下调用。

    Returns:
        RuleFinding（命中时） / None（未命中或 LLM 失败）
    """
    if not _FALLBACK_ENABLED:
        return None
    # 前置过滤：无疑似模式直接返回，避免无谓 LLM 调用
    if not _SUSPICIOUS_ABSOLUTE.search(answer or ""):
        return None

    data = _call_llm(
        _ABSOLUTE_USER_TMPL.format(answer=answer[:1000]),
        _ABSOLUTE_SYSTEM,
    )
    if not data or not data.get("hit"):
        return None
    words = data.get("words", [])
    word_str = "/".join(words) if words else "（LLM 判定，未返回具体词）"
    return RuleFinding(
        rule="absolute_words_llm",
        severity="critical",
        message=f"[LLM 兜底] 检测到绝对化/承诺性表述: {word_str}（合规一票否决）",
        detail={"words": words, "source": "llm_fallback"},
    )


# ---------- 免责声明 LLM 兜底 ----------

_DISCLAIMER_SYSTEM = (
    "你是金融合规审核员。判断答案是否构成投资建议且缺少有效免责声明。"
    "有效免责声明包括但不限于：不构成投资建议/仅供参考/风险提示/入市需谨慎/"
    "据此操作风险自担。变形表述如'以上不构成建议''此文不含投资建议'也算有效。"
    "只输出 JSON：{\"is_recommendation\": bool, \"has_disclaimer\": bool, \"reason\": str}"
)

_DISCLAIMER_USER_TMPL = """判断以下答案：

答案：
{answer}

判断要求：
1. is_recommendation：是否构成投资建议（含买入/卖出/加仓/减仓/强烈看多等）
2. has_disclaimer：是否包含有效的免责声明（含变形表述）
3. 只输出 JSON"""


def llm_check_disclaimer(question: str, answer: str) -> Optional[RuleFinding]:
    """LLM 兜底检测免责声明缺失。仅在疑似构成建议但关键词未匹配到免责时调用。

    Returns:
        RuleFinding（命中时） / None（未命中或 LLM 失败）
    """
    if not _FALLBACK_ENABLED:
        return None
    # 前置过滤：答案未疑似构成投资建议，直接返回
    if not _SUSPICIOUS_DISCLAIMER_ABSENT.search(answer or ""):
        return None

    data = _call_llm(
        _DISCLAIMER_USER_TMPL.format(answer=answer[:1000]),
        _DISCLAIMER_SYSTEM,
    )
    if not data:
        return None
    if not data.get("is_recommendation"):
        return None
    if data.get("has_disclaimer"):
        return None
    return RuleFinding(
        rule="disclaimer_llm",
        severity="critical",
        message=f"[LLM 兜底] 答案构成投资建议但缺少有效免责声明（合规一票否决）",
        detail={"reason": data.get("reason", ""), "source": "llm_fallback"},
    )
