# -*- coding: utf-8 -*-
"""
压测 AI 失败分析服务。

读取执行指标 + 失败样本 → 构建 prompt → 调用 AIModelService（OpenAI 兼容）→ 返回分析文本。
支持流式输出（yield 逐块）和非流式（一次性返回）。
"""

import logging
from typing import Dict, List, Generator, Optional

logger = logging.getLogger(__name__)


def build_analysis_prompt(stats: List[Dict], summary: Dict, verdict: str = '', verdict_details: List[Dict] = None) -> str:
    """
    构建压测 AI 分析的提示词。

    :param stats: PerfRequestStat 序列化列表
    :param summary: 执行汇总（tps, error_rate, avg_response_time 等）
    :param verdict: 验收判定（PASSED/FAILED/NOT_EVALUATED）
    :param verdict_details: 验收明细
    :return: 提示词字符串
    """
    # 最慢的 5 个接口
    sorted_by_rt = sorted(stats, key=lambda s: s.get('avg_response_time', 0), reverse=True)
    slowest = [
        f"  - {s.get('step_name', '?')}: 平均{s.get('avg_response_time', 0):.0f}ms, P95={s.get('p95', 0):.0f}ms"
        for s in sorted_by_rt[:5]
    ]

    # 错误率最高的 5 个接口
    sorted_by_err = sorted(stats, key=lambda s: s.get('error_rate', 0), reverse=True)
    errors = [
        f"  - {s.get('step_name', '?')}: 错误率{s.get('error_rate', 0):.1f}%, 失败{s.get('failed', 0)}次"
        for s in sorted_by_err[:5] if s.get('error_rate', 0) > 0
    ]

    # 验收判定
    verdict_text = ''
    if verdict and verdict != 'NOT_EVALUATED':
        verdict_text = f"\n验收判定: {verdict}"
        if verdict_details:
            fail_items = [d for d in verdict_details if d.get('result') == 'FAIL']
            if fail_items:
                verdict_text += "\n未达标项:\n" + '\n'.join([
                    f"  - {d.get('step')}: {d.get('metric')} 目标{d.get('target')}{d.get('unit','')}, 实际{d.get('actual')}{d.get('unit','')}"
                    for d in fail_items
                ])

    prompt = f"""你是一位性能测试专家。请分析以下压测结果，给出瓶颈定位和优化建议。

## 压测汇总
- 整体 TPS: {summary.get('tps', 0):.1f}
- 平均响应时间: {summary.get('avg_response_time', 0):.0f}ms
- 错误率: {summary.get('error_rate', 0):.2f}%
- 总请求数: {summary.get('total_requests', 0)}
- 成功: {summary.get('success_requests', 0)}, 失败: {summary.get('failed_requests', 0)}
{verdict_text}

## 最慢接口（Top 5）
{chr(10).join(slowest) if slowest else '  无数据'}

## 错误接口
{chr(10).join(errors) if errors else '  无错误接口'}

请按以下格式回答：
1. **瓶颈定位**：哪些接口/环节是性能瓶颈，为什么
2. **优化建议**：具体可操作的优化措施（连接池/缓存/SQL/并发等）
3. **风险评估**：当前性能是否存在线上风险

回答要简洁、具体、可操作。"""
    return prompt


def get_ai_config():
    """获取 AI 模型配置（场景绑定优先，回退 writer）"""
    from apps.requirement_analysis.scene_binding import resolve_scene_config
    return resolve_scene_config('perf_ai')


def analyze_stream(stats: List[Dict], summary: Dict, verdict: str = '', verdict_details: List[Dict] = None) -> Generator[str, None, None]:
    """
    流式分析：逐块 yield LLM 输出。

    内部用 asyncio.run 包装 AIModelService 的 async 流式方法，
    通过 queue 将 async 迭代转为同步 generator。

    :raises RuntimeError: 如果无可用 AI 配置或调用失败
    """
    config = get_ai_config()
    if not config:
        raise RuntimeError('未找到激活的 AI 模型配置（role=writer），请先在系统配置中添加')

    prompt = build_analysis_prompt(stats, summary, verdict, verdict_details)
    messages = [{'role': 'user', 'content': prompt}]

    import asyncio
    import queue
    import threading

    q = queue.Queue()
    _SENTINEL = object()

    async def _run():
        from apps.requirement_analysis.models import AIModelService
        try:
            async for chunk in AIModelService.call_openai_compatible_api_stream(config, messages):
                if chunk:
                    q.put(chunk)
        except Exception as e:
            logger.error(f"AI 分析流式调用失败: {e}", exc_info=True)
            q.put(e)
        finally:
            q.put(_SENTINEL)

    # 后台线程跑事件循环，主线程 yield
    def _runner():
        asyncio.run(_run())

    t = threading.Thread(target=_runner, daemon=True)
    t.start()

    while True:
        item = q.get(timeout=120)  # 120s 超时
        if item is _SENTINEL:
            break
        if isinstance(item, Exception):
            raise RuntimeError(f'AI 分析调用失败: {item}')
        yield item


def analyze_full(stats: List[Dict], summary: Dict, verdict: str = '', verdict_details: List[Dict] = None) -> str:
    """
    非流式分析：一次性返回完整文本。
    """
    return ''.join(analyze_stream(stats, summary, verdict, verdict_details))
