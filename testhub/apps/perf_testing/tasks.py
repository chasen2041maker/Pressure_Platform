# -*- coding: utf-8 -*-
"""
perf_testing Celery 任务。

AI 分析任务：异步执行 LLM 调用，结果缓存到 Redis，通过 channel_layer 推送进度。
"""

import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, ignore_result=True)
def analyze_perf_execution_task(self, execution_id):
    """
    异步执行压测 AI 分析。

    1. 读取执行指标
    2. 调用 AI 流式分析
    3. 结果缓存到 Redis（30 分钟）
    4. 通过 channel_layer 推送完成事件
    """
    import json
    from django.core.cache import cache
    from apps.perf_testing.models import PerfExecution, PerfRequestStat
    from apps.perf_testing.services.ai_analysis import analyze_stream

    cache_key = f'perf:ai_analysis:{execution_id}'

    try:
        execution = PerfExecution.objects.select_related('scenario').get(id=execution_id)
        from django.db.models import F
        stats = list(PerfRequestStat.objects.filter(execution_id=execution_id).annotate(
            # 模型字段已重命名为 avg_rt/p95_rt，此处别名回旧键，保持下游消费键不变
            avg_response_time=F('avg_rt'), p95=F('p95_rt')
        ).values(
            'step_name', 'avg_response_time', 'p95', 'error_rate', 'failed', 'total'
        ))
        summary = execution.summary or {}
        verdict = execution.verdict or ''
        verdict_details = execution.verdict_details or []

        if not stats:
            cache.set(cache_key, json.dumps({'error': '无执行指标数据'}), timeout=1800)
            return

        # 流式收集 + 推送
        full_text = []
        for chunk in analyze_stream(stats, summary, verdict, verdict_details):
            full_text.append(chunk)
            # 可选：通过 WS 推送逐块（前端如果连了 WS）
            # 目前由 SSE 端点直接同步 yield，这里只做缓存

        result = json.dumps({'analysis': ''.join(full_text)}, ensure_ascii=False)
        cache.set(cache_key, result, timeout=1800)  # 30 分钟
        logger.info(f"AI 分析完成 execution_id={execution_id}, 缓存已写入")

    except Exception as e:
        logger.error(f"AI 分析任务失败 execution_id={execution_id}: {e}", exc_info=True)
        cache.set(cache_key, json.dumps({'error': str(e)}, ensure_ascii=False), timeout=1800)


@shared_task(bind=True, ignore_result=True)
def comparison_ai_analysis_task(self, report_id):
    """多轮对照报告 AI 分析：压缩快照 → LLM 对照分析 → 写回 ai_analysis。"""
    import asyncio
    from apps.perf_testing.models import PerfComparisonReport
    from apps.perf_testing.services.compare_report import trim_snapshot_for_ai
    from apps.requirement_analysis.scene_binding import resolve_scene_config
    from apps.requirement_analysis.models import AIModelService

    report = PerfComparisonReport.objects.filter(id=report_id).first()
    if not report:
        logger.warning(f"对照报告不存在 report_id={report_id}")
        return

    def _finish(markdown):
        report.ai_analysis = markdown
        report.save(update_fields=['ai_analysis'])

    try:
        config = resolve_scene_config('perf_ai')
        if not config:
            _finish('> AI 分析失败：未找到激活的 AI 模型配置，请先在系统配置中添加')
            return

        matrix = trim_snapshot_for_ai(report.snapshot or {})
        prompt = f"""你是性能测试专家。以下是一次多轮压测执行的对照数据（含相对基准的变化率 Δ%）：

{matrix}

请输出 markdown 格式的对照分析，严格包含三段：
## 结论
（总体性能趋势，一轮内是否劣化）
## 劣化点
（哪些指标/接口相对基准明显变差，引用具体数值；无则写“未发现明显劣化”）
## 建议
（可操作的下一步，如定位瓶颈/调整参数/增加轮次）

要求简洁、具体，不要重复罗列原始数据。"""

        messages = [{'role': 'user', 'content': prompt}]
        result = asyncio.run(AIModelService.call_openai_compatible_api(config, messages))
        content = ''
        if isinstance(result, dict):
            choices = result.get('choices') or []
            if choices:
                content = (choices[0].get('message') or {}).get('content') or ''
        if not content:
            _finish('> AI 分析失败：模型未返回有效内容')
            return
        _finish(content.strip())
        logger.info(f"对照报告 AI 分析完成 report_id={report_id}")

    except Exception as e:
        logger.error(f"对照报告 AI 分析失败 report_id={report_id}: {e}", exc_info=True)
        try:
            _finish(f'> AI 分析失败：{e}')
        except Exception:  # noqa: BLE001
            pass
