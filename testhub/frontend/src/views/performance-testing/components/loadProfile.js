/**
 * 压测曲线点计算工具（pure function，便于 DB-free 单测）
 *
 * 四种压力模型对应的曲线点序列：
 *   - CONCURRENCY：固定并发（带 ramp-up 升窗）   → 0 → ramp → dur
 *   - RAMPING    ：阶梯加压                       → 累加每个 stage 的 target
 *   - RPS        ：固定 RPS（恒值水平线）           → 0 → dur
 *   - SPIKE      ：尖峰冲击（base ↔ peak 交替）     → 多次尖峰
 *
 * 所有数值字段对 NaN/空值都做了兜底，确保返回点序列不会因输入异常导致渲染崩溃。
 *
 * @param {Object} form  包含 model/concurrency/duration/ramp_up/stages/target_rps/
 *                       baseline_concurrency/spike_concurrency/spike_duration/spike_times 的负载配置
 * @returns {Array<{t:number, u:number}>}  时间-并发/RPS 坐标点
 */
export function computeLoadProfilePoints(form = {}) {
  const pts = []
  const m = form.model
  if (m === 'CONCURRENCY') {
    const dur = Number(form.duration) || 0
    const ramp = Number(form.ramp_up) || 0
    const conc = Number(form.concurrency) || 0
    pts.push({ t: 0, u: 0 })
    if (ramp > 0 && ramp < dur) pts.push({ t: ramp, u: conc })
    pts.push({ t: dur, u: conc })
  } else if (m === 'RAMPING') {
    let t = 0
    pts.push({ t: 0, u: 0 })
    for (const s of form.stages || []) {
      const d = Number(s.duration) || 0
      const target = Number(s.target) || 0
      if (d > 0) {
        pts.push({ t: t + d, u: target })
        t += d
      }
    }
    if (pts.length === 1) pts.push({ t: 1, u: 0 })
  } else if (m === 'RPS') {
    const dur = Number(form.duration) || 0
    const rps = Number(form.target_rps) || 0
    pts.push({ t: 0, u: rps })
    pts.push({ t: dur, u: rps })
  } else if (m === 'SPIKE') {
    const base = Number(form.baseline_concurrency) || 0
    const peak = Number(form.spike_concurrency) || 0
    const hold = Number(form.spike_duration) || 0
    const times = Number(form.spike_times) || 0
    let cur = base
    pts.push({ t: 0, u: base })
    for (let i = 0; i < times; i++) {
      pts.push({ t: cur, u: peak }); cur += hold
      pts.push({ t: cur, u: base }); cur += hold
    }
  }
  return pts
}

/**
 * 为 echarts 计算坐标轴上下限，留出 5%~20% padding 让曲线不贴边
 *
 * @param {Array<{t:number,u:number}>} pts
 * @returns {{xMax:number, yMax:number}}
 */
export function computeAxisBounds(pts = []) {
  if (!pts.length) return { xMax: 1, yMax: 1 }
  let maxT = 0
  let maxU = 0
  for (const p of pts) {
    if (p.t > maxT) maxT = p.t
    if (p.u > maxU) maxU = p.u
  }
  return {
    xMax: maxT > 0 ? maxT * 1.05 : 1,
    yMax: maxU > 0 ? maxU * 1.2 : 1
  }
}
