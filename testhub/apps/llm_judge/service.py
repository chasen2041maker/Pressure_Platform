"""JudgeService facade：从 judge_engine.service 导入，供 views.py 和 tasks.py 调用。"""
from .judge_engine.service import JudgeService

__all__ = ['JudgeService']
