"""智能评分器 Celery 任务：批量评分异步执行 + 进度回写 + 暂停续跑。

对齐 apps/testcases/tasks.py 的写法（@shared_task(bind=True) + 状态字段回写）。
暂停/续跑由 JudgeBatch.is_paused / JudgeBatch.status 控制位驱动：
- 用户点暂停：ViewSet.pause() 更新 is_paused=True, status='paused'；本任务每条循环前检测到即中断。
- 用户点继续：ViewSet.resume() 把 paused/partial/failed/pending → running，并再次调 .delay()；
  本任务进入后从 batch.scored 位置继续，已评结果存在 batch.results_buffer / batch.error_count。
"""
import logging
from celery import shared_task
from django.utils import timezone

from .models import JudgeBatch

logger = logging.getLogger(__name__)


@shared_task(bind=True)
def score_batch_task(self, batch_id: int):
    """批量评分：循环调 JudgeService.score_single()，支持暂停续跑。

    JudgeRecord 的具体落库由 JudgeService.score_single() 内部完成（含 batch 外键），
    此处不再重复造数据。
    """
    from .service import JudgeService

    batch = JudgeBatch.objects.get(id=batch_id)

    # ---- 1. 初始化（支持 resume 续跑） ----
    if batch.started_at is None:
        batch.started_at = timezone.now()
    if batch.status != 'running':
        batch.status = 'running'
    batch.is_paused = False
    try:
        batch.celery_task_id = self.request.id or ''
    except Exception:
        pass
    batch.save(update_fields=['status', 'celery_task_id', 'started_at', 'is_paused'])

    try:
        service = JudgeService()
        total = len(batch.cases_data)
        batch.total = total
        batch.save(update_fields=['total'])

        # resume 现场恢复：从上次 scored 位置继续
        start_i = int(batch.scored or 0)
        results = list(batch.results_buffer or [])
        error_cnt = int(batch.error_count or 0)
        # 兜底：results 长度必须等于 start_i
        if len(results) < start_i:
            results.extend([{'error': 'resume_missing_result', 'case_index': i}
                            for i in range(len(results), start_i)])
        elif len(results) > start_i:
            # 理论不会发生；截到 start_i 避免重复算
            results = results[:start_i]

        # ---- 2. 循环评分（每条开始前检查暂停控制位） ----
        for global_i in range(start_i, total):
            # 暂停检查（每条都查一次；高并发下确保立即生效）
            batch.refresh_from_db(fields=['is_paused'])
            if batch.is_paused:
                logger.info(f'[JudgeBatch#{batch_id}] 暂停触发：位置 {global_i}/{total}，保存现场')
                # 局部汇总（已评分的 results）+ 落盘
                try:
                    summary = service.summarize_batch(results) if results else {
                        'mean_score': 0, 'std_dev': 0, 'safety_pass_rate': 0,
                        'critical_success_rate': 0, 'gate_zone': 'gray', 'blocked': False,
                    }
                except Exception as sum_err:
                    logger.warning(f'[JudgeBatch#{batch_id}] (paused) summarize 失败: {sum_err}')
                    summary = {'mean_score': 0, 'std_dev': 0, 'safety_pass_rate': 0,
                               'critical_success_rate': 0, 'gate_zone': 'gray', 'blocked': False}

                JudgeBatch.objects.filter(pk=batch.id).update(
                    scored=len(results),
                    progress=int(len(results) / max(1, total) * 100),
                    results_buffer=results,
                    error_count=error_cnt,
                    mean_score=summary['mean_score'],
                    std_dev=summary['std_dev'],
                    safety_pass_rate=summary['safety_pass_rate'],
                    critical_success_rate=summary['critical_success_rate'],
                    gate_zone=summary['gate_zone'],
                    blocked=bool(summary.get('blocked', False)),
                    status='paused',
                    is_paused=True,
                )
                try:
                    self.update_state(state='PAUSED', meta={
                        'current': len(results), 'total': total,
                        'progress': int(len(results) / max(1, total) * 100),
                    })
                except Exception:
                    pass
                return {
                    'batch_id': batch.id,
                    'status': 'paused',
                    'scored': len(results),
                    'total': total,
                    'mean_score': summary['mean_score'],
                    'error_cnt': error_cnt,
                    'paused_at_case': global_i,
                }

            # 真正评当前条（global_i 对应 case index）
            case = batch.cases_data[global_i]
            try:
                resp = service.score_single(case, batch=batch, created_by=batch.created_by)
                results.append(resp)
            except Exception as exc:
                logger.warning(f'[JudgeBatch#{batch_id}] case {global_i+1} 评分失败: {exc}')
                results.append({'error': str(exc), 'case_index': global_i})
                error_cnt += 1

            # 进度 + results_buffer 持久化（确保 resume 可用；每 10 条或最后一条回写）
            scored_now = global_i + 1
            progress_now = int(scored_now / max(1, total) * 100)
            flush = (scored_now == total) or (scored_now % 10 == 0)
            update_fields = ['scored', 'progress']
            batch.scored = scored_now
            batch.progress = progress_now
            if flush:
                batch.results_buffer = results
                batch.error_count = error_cnt
                update_fields.extend(['results_buffer', 'error_count'])
            batch.save(update_fields=update_fields)
            try:
                self.update_state(state='PROGRESS', meta={
                    'current': scored_now, 'total': total, 'progress': progress_now,
                })
            except Exception:
                pass

        # ---- 3. 最终汇总与 status ----
        try:
            summary = service.summarize_batch(results)
        except Exception as sum_err:
            logger.warning(f'[JudgeBatch#{batch_id}] summarize 失败，兜底 0：{sum_err}')
            summary = {
                'mean_score': 0, 'std_dev': 0, 'safety_pass_rate': 0,
                'critical_success_rate': 0, 'gate_zone': 'gray', 'blocked': False,
            }
        batch.mean_score = summary['mean_score']
        batch.std_dev = summary['std_dev']
        batch.safety_pass_rate = summary['safety_pass_rate']
        batch.critical_success_rate = summary['critical_success_rate']
        batch.gate_zone = summary['gate_zone']
        batch.blocked = bool(summary.get('blocked', False))

        if total == 0:
            batch.status = 'failed'
            batch.error_message = '0 条用例'
        elif error_cnt == 0:
            batch.status = 'completed'
            batch.error_message = ''
        elif error_cnt >= total:
            batch.status = 'failed'
            batch.error_message = f'全部 {total} 条失败'
        else:
            batch.status = 'partial'
            batch.error_message = f'{error_cnt}/{total} 条失败'
        batch.results_buffer = results
        batch.error_count = error_cnt
        batch.is_paused = False
        batch.completed_at = timezone.now()
        batch.save()

        return {
            'batch_id': batch.id,
            'status': batch.status,
            'scored': batch.scored,
            'total': batch.total,
            'mean_score': batch.mean_score,
            'error_cnt': error_cnt,
        }
    except Exception as exc:
        logger.exception(f'[JudgeBatch#{batch_id}] 批量评分失败')
        JudgeBatch.objects.filter(pk=batch.id).update(
            status='failed',
            error_message=str(exc),
            completed_at=timezone.now(),
            results_buffer=results if 'results' in locals() else list(batch.results_buffer or []),
            error_count=error_cnt if 'error_cnt' in locals() else int(batch.error_count or 0),
            is_paused=False,
        )
        raise
