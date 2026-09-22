"""Rubric 加载与访问：从 YAML 读取评分标准，提供维度权重、否决项、评分/门禁配置。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import yaml

DEFAULT_RUBRIC_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "rubric_templates", "finance_rubric_v1.0.yaml",
)


@dataclass
class Dimension:
    id: str
    name: str
    type: str                      # "score" | "bool"
    weight: float
    min: int = 1
    max: int = 5
    anchors: dict = field(default_factory=dict)
    veto: bool = False
    rule_backed: bool = False
    description: str = ""


@dataclass
class Group:
    id: str
    name: str
    weight: float
    dimensions: list = field(default_factory=list)


class Rubric:
    # domain -> 中文领域描述映射（供 Judge prompt 动态使用）
    DOMAIN_LABELS = {
        "finance": "金融",
        "qa": "通用问答与知识检索",
        "customer_service": "客服与对话",
        "custom": "自定义业务",
    }

    def __init__(self, raw: dict):
        self.raw = raw
        self.version = raw.get("version")
        self.name = raw.get("name", "rubric")
        # domain：优先顶层 domain，其次 loader 注入的 _rubric_domain，兜底 custom
        self.domain = raw.get("domain") or raw.get("_rubric_domain") or "custom"
        self.description = raw.get("description", "")
        self.groups = [
            Group(
                g["id"], g["name"], float(g.get("weight", 0)),
                [Dimension(**d) for d in g.get("dimensions", [])],
            )
            for g in raw.get("groups", [])
        ]
        self.dim_map: dict[str, Dimension] = {
            d.id: d for g in self.groups for d in g.dimensions
        }
        self.score_dims = [d for d in self.dim_map.values() if d.type == "score"]
        self.veto_dims = [d for d in self.dim_map.values() if d.veto]
        self.rules: dict = raw.get("rules", {})
        self.timeliness: dict = raw.get("timeliness", {})
        self.numeric_gt: dict = raw.get("numeric_gt", {})
        self.scoring: dict = raw.get("scoring", {})
        self.gate: dict = raw.get("gate", {})

    @staticmethod
    def load(path: Optional[str] = None) -> "Rubric":
        p = path or DEFAULT_RUBRIC_PATH
        # 兼容 Django settings: 优先用 JUDGE_RUBRIC_DIR 配置
        with open(p, "r", encoding="utf-8") as f:
            return Rubric(yaml.safe_load(f))

    def weight_map(self) -> dict[str, float]:
        return {d.id: d.weight for d in self.dim_map.values()}

    def score_anchors_text(self) -> str:
        lines = []
        for d in self.score_dims:
            anchors = "；".join(f"{k}分：{v}" for k, v in sorted(d.anchors.items()))
            lines.append(f"- {d.id}（{d.name}，权重 {d.weight:.0%}，{d.min}-{d.max}分）: {anchors}")
        return "\n".join(lines)

    def veto_text(self) -> str:
        lines = [f"- {d.name}: {d.description}" for d in self.veto_dims]
        return "\n".join(lines) if lines else "（无）"

    def domain_label(self) -> str:
        """返回领域中文描述，供 Judge prompt 动态拼接。"""
        return self.DOMAIN_LABELS.get(self.domain, self.domain)
