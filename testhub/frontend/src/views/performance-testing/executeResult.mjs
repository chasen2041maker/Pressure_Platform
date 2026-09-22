// 性能测试「执行结果归一化」纯函数（无 Vue/i18n 依赖，可被 node 单测直接 import）。
//
// 后端 execute 接口在 preflight 失败时返回 {passed, errors, warnings, estimated}
// （无 ok 字段）；而弹窗模板读的是 preflight.ok。归一化后统一产出 ok，
// 避免仅有 warnings（passed=true）的场景被误判为失败、且「开始」按钮被禁用。
export function normalizePreflight(pf) {
  const p = pf || {}
  return {
    ok: Boolean(p.passed),
    errors: Array.isArray(p.errors) ? p.errors : [],
    warnings: Array.isArray(p.warnings) ? p.warnings : [],
    estimated: p.estimated ?? null,
  }
}

export function normalizeExecuteResult(res) {
  const data = (res && res.data) || res || {}
  const started = Boolean(data.execution)
  return { started, ...normalizePreflight(data.preflight) }
}
