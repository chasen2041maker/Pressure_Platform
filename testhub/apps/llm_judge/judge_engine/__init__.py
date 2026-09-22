"""LLM 评分引擎核心包（从 finance_judge 迁移改造，适配 Django）。

模块说明：
- config.py: 从 Django settings 读取配置
- models.py: Pydantic 数据模型（与 finance_judge 兼容）
- rubric_loader.py: Rubric 加载（DB 优先 → YAML fallback）
- rules.py: 规则引擎
- rules_llm.py: 规则 LLM 兜底
- judge.py: LLM Judge 引擎
- scorer.py: 最终分合成
- gateway.py: 门禁分区
- cache.py: Django cache 封装
- gt_provider.py: ground_truth 供给
- service.py: 对外统一入口
"""
__version__ = '1.0.0'
