<template>
  <div class="perf-report" v-loading="loading">
    <!-- ---------- 顶栏 ---------- -->
    <div class="report-head">
      <div class="head-left">
        <el-button link :icon="ArrowLeft" @click="goBack">{{ t('performanceTesting.common.back') }}</el-button>
        <el-divider direction="vertical" />
        <span class="exec-no">{{ execution.execution_no }}</span>
        <span class="scenario-name">{{ execution.scenario_name }}</span>
        <el-tag :type="statusTagType(execution.status)" size="small">
          {{ execution.status ? t('performanceTesting.status.' + execution.status) : '-' }}
        </el-tag>
        <el-tag :type="slaTagType(execution.sla_result)" size="small" effect="plain">
          SLA {{ execution.sla_result ? t('performanceTesting.sla.' + execution.sla_result) : '-' }}
        </el-tag>
        <el-tag
          v-if="execution.verdict && execution.verdict !== 'NOT_EVALUATED'"
          :type="execution.verdict === 'PASSED' ? 'success' : 'danger'"
          size="small"
        >
          {{ execution.verdict === 'PASSED' ? '验收通过' : '验收未通过' }}
        </el-tag>
      </div>
      <div class="head-right">
        <div class="report-actions" role="group" aria-label="报告操作">
          <el-button :icon="Refresh" :loading="regenerating" :disabled="!reportReady" @click="handleRegenerate">
            {{ t('performanceTesting.report.regenerate') }}
          </el-button>
          <el-button :icon="Link" :disabled="!reportReady || !execution.report_url" @click="openHtml">预览 HTML</el-button>
          <el-button :icon="Share" :disabled="!reportReady || !execution.report_url" @click="openShare">
            {{ t('performanceTesting.share.button') }}
          </el-button>
        </div>
        <div class="report-downloads" role="group" aria-label="下载报告与明细">
          <el-button :icon="Download" :disabled="!htmlDownloadReady" @click="exportReport('html')">
            {{ t('performanceTesting.report.exportHtml') }}
          </el-button>
          <el-button :disabled="!reportReady || reportIsActive" @click="exportReport('json')">导出 JSON</el-button>
          <el-button :disabled="!reportReady || reportIsActive" @click="exportReport('csv')">导出 CSV 汇总</el-button>
          <el-button :icon="Download" :disabled="!reportReady || !execution.has_raw_detail" @click="downloadRaw">
            {{ t('performanceTesting.report.downloadRaw') }}
          </el-button>
        </div>
      </div>
    </div>

    <el-alert v-if="loadError" type="error" :closable="false" show-icon class="mb12" :title="loadError">
      <el-button link type="primary" @click="loadAll">重试加载报告</el-button>
    </el-alert>
    <el-alert v-if="isK6 && (execution.summary?.sample_data_incomplete || execution.summary?.sample_persistence?.stopped_due_to_storage)"
      :type="execution.summary?.sample_data_incomplete ? 'error' : 'warning'" :closable="false" show-icon class="mb12"
      :title="execution.error_message || (execution.summary?.sample_data_incomplete ? `采样数据不完整：缺失 ${execution.summary.sample_missing_count ?? '未知数量'} 条时序采样。已收到的请求统计及原始明细保留，整体 SLA 未评估。` : '存储失败导致提前停止；采样后来已恢复，业务负载未完整执行，整体 SLA 未评估。')" />
    <el-tabs v-model="activeTab" class="report-tabs" @tab-change="onTabChange">
      <!-- ================= 概览 ================= -->
      <el-tab-pane :label="t('performanceTesting.report.tabOverview')" name="overview">
        <el-alert v-if="isK6 && (execution.summary?.sla_abort || execution.summary?.completion_reason)"
          type="warning" :closable="false" show-icon class="mb12"
          :title="execution.summary?.sla_abort ? `SLA 持续超限，于 ${metricValue(execution.summary.sla_abort.elapsed_seconds ?? execution.summary.sla_abort.ts_offset)} 秒中止；部分结果保留，整体未评估。` : (SLA_REASONS[execution.summary.completion_reason] || execution.summary.completion_reason)" />
        <MetricCards :items="overviewCards" :cols="6" class="metric-row" />
        <template v-if="isK6">
          <NativeVUObservations :execution-id="executionId" :observations="nativeObservations"
            :summary="execution.summary?.native_vu" :configured-vus="execution.load_snapshot?.concurrency"
            :script-users="execution.summary?.max_concurrency" script-label="脚本活动用户峰值" mode="report" />
          <MetricCards :items="k6RequestCards" :cols="4" class="metric-row" />
          <WebSocketMetrics v-if="hasWebsocket" :metrics="execution.summary?.websocket || null" :steps="execution.steps_snapshot || []" />
          <SseMetrics v-if="hasSse" :metrics="execution.summary?.sse || null" :steps="execution.steps_snapshot || []" />
          <ExecutionPolicyMetrics :metrics="execution.summary?.execution_policy || null" :steps="execution.steps_snapshot || []" />
          <ReminderRecoveryStatus :value="execution.resource_recovery" :loading="!reportReady || recoveryLoading" @refresh="refreshRecovery" />
          <el-alert v-if="hasProtocolStreams" type="info" :closable="false" :title="t('performanceTesting.sse.mixedNotice')" />
          <el-alert v-else type="info" :closable="false" show-icon
            title="已发起按 HTTP 调用开始计数，不保证服务端已收到；已完成以收到请求结果计数。未完成为已发起减已完成，可能来自到时结束、手动停止或异常中止。业务统计排除前置、登录及刷新，失败率按已完成业务请求计算，每请求只计一次成败。" />
          <el-alert v-if="hasProtocolStreams" type="info" :closable="false" :title="t('performanceTesting.sse.mixedRateNotice')" />
          <el-alert v-else type="info" :closable="false" show-icon class="mt12"
            :title="`完整执行轮次不代表成功事务数，一轮内可能有失败请求。业务 RPS＝已完成业务请求数÷引擎启动至清理结束的时间；包含容器启动、登录与刷新、思考时间、采集及停止清理耗时。累计 P95/P99 来自对应请求直方图估算，不是每秒分位数的平均；请求与响应字节数未采集。CPU：${cpuDescription}。单项资源指标不能独立证明发压器容量。`" />
          <el-card shadow="never" class="interface-card">
            <template #header><strong>每个接口测了多少次</strong></template>
            <p class="interface-note">{{ interfaceStatsNotice }}</p>
            <el-table :data="k6InterfaceRows" class="k6-interface-table" border size="small" empty-text="尚未取得接口明细">
              <el-table-column prop="stepId" label="步骤 ID" width="105" />
              <el-table-column label="接口名称" min-width="190">
                <template #default="{ row }">
                  {{ row.name }} · #{{ row.stepId }}
                  <el-tag size="small" type="info">{{ K6_PHASE_LABELS[row.phase] }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="API 地址" min-width="290">
                <template #default="{ row }"><code class="interface-path">{{ row.requestPath || '历史记录未保存地址' }}</code></template>
              </el-table-column>
              <el-table-column prop="method" label="方法" width="80" />
              <el-table-column v-if="hasProtocolStreams" :label="t('performanceTesting.websocket.protocolLatency')" min-width="180"><template #default="{ row }">{{ t(protocolLatencyKey(row.protocol)) }}</template></el-table-column>
              <el-table-column :label="hasProtocolStreams ? '步骤次数（已完成）' : '请求次数（已完成）'" min-width="140" align="right">
                <template #default="{ row }">{{ formatK6Count(row.total) }}</template>
              </el-table-column>
              <el-table-column label="成功次数" min-width="100" align="right">
                <template #default="{ row }"><span class="good-val">{{ formatK6Count(row.success) }}</span></template>
              </el-table-column>
              <el-table-column label="失败次数" min-width="100" align="right">
                <template #default="{ row }"><strong :class="{ 'bad-val': row.failed > 0 }">{{ formatK6Count(row.failed) }}</strong></template>
              </el-table-column>
              <el-table-column prop="attribution" label="归属说明" min-width="150" />
              <el-table-column prop="avg_rt" :label="hasProtocolStreams ? '平均步骤耗时 ms' : '平均响应 ms'" width="120"><template #default="{ row }">{{ metricValue(row.avg_rt) }}</template></el-table-column>
              <el-table-column prop="p95_rt" label="累计 P95 ms（估算）" width="170"><template #default="{ row }">{{ metricValue(row.p95_rt) }}</template></el-table-column>
              <el-table-column prop="tps" :label="hasProtocolStreams ? businessRateLabel : '请求 RPS'" width="110"><template #default="{ row }">{{ metricValue(row.tps) }}</template></el-table-column>
              <el-table-column prop="errorRate" label="失败率" width="100" align="right" />
              <el-table-column prop="failureReason" label="失败原因与次数" min-width="290" />
            </el-table>
            <div class="k6-interface-cards">
              <article v-for="row in k6InterfaceRows" :key="row.key" class="k6-interface-item">
                <div class="interface-item-title"><strong>{{ row.name }} · #{{ row.stepId }}</strong><el-tag size="small" effect="plain">{{ row.method }}</el-tag></div>
                <p class="interface-path"><code>{{ row.requestPath || '历史记录未保存地址' }}</code></p>
                <el-tag size="small" type="info">{{ K6_PHASE_LABELS[row.phase] }}</el-tag>
                <p v-if="hasProtocolStreams">{{ t(protocolLatencyKey(row.protocol)) }}</p>
                <dl class="interface-counts">
                  <div><dt>{{ hasProtocolStreams ? '步骤（已完成）' : '请求（已完成）' }}</dt><dd>{{ formatK6Count(row.total) }}</dd></div>
                  <div><dt>平均 / P95估算（ms）</dt><dd>{{ metricValue(row.avg_rt) }} / {{ metricValue(row.p95_rt) }}</dd></div>
                  <div><dt>{{ hasProtocolStreams ? businessRateLabel : '请求 RPS' }}</dt><dd>{{ metricValue(row.tps) }}</dd></div>
                  <div><dt>成功次数</dt><dd :class="{ 'good-val': row.success != null }">{{ formatK6Count(row.success) }}</dd></div>
                  <div><dt>失败次数</dt><dd :class="{ 'bad-val': row.failed > 0 }">{{ formatK6Count(row.failed) }}</dd></div>
                </dl>
                <p v-if="row.attribution" class="interface-note">{{ row.attribution }}</p>
                <p class="interface-failure"><b>失败原因：</b>{{ row.failureReason }}</p>
              </article>
              <el-empty v-if="!k6InterfaceRows.length" description="尚未取得接口明细" :image-size="60" />
            </div>
            <p v-if="hasProtocolStreams" class="interface-note">{{ t('performanceTesting.sse.mixedNotice') }}</p>
      <p v-else class="interface-note">成功 + 失败 = 已完成请求；不含尚未取得结果的请求。前置、登录和刷新单独标记，不计入业务总计。历史记录若只保存“网络传输失败”，不会推测更细原因。</p>
          </el-card>
        </template>

        <el-card v-if="isK6 && execution.load_snapshot?._purpose === 'debug'" shadow="never" class="debug-card">
          <template #header><strong>{{ t('performanceTesting.auth.debugTitle') }}</strong></template>
          <p class="interface-note">{{ t('performanceTesting.auth.debugNotice') }}</p>
          <el-alert v-if="execution.summary?.debug_details?.truncated" type="warning" :closable="false" :title="t('performanceTesting.auth.truncated')" />
          <el-empty v-if="!execution.summary?.debug_details" :description="t('performanceTesting.auth.notCollected')" />
          <el-collapse v-else>
            <el-collapse-item v-for="row in k6DebugRows" :key="row.key" :name="row.key">
              <template #title><div class="debug-step-title"><b>{{ row.name }} · #{{ row.step_id }}</b><el-tag size="small">{{ K6_PHASE_LABELS[row.phase] }}</el-tag><span :class="{ 'bad-val': ['failed', 'not_sent', 'transport_failed', 'http_failed', 'assertion_failed', 'extraction_failed'].includes(row.outcome) }">{{ K6_DEBUG_LABELS[row.outcome] || '未提供诊断' }}</span><span v-if="row.protocol === 'WEBSOCKET'">WS · {{ ['command', 'event'].includes(row.stage) ? `${t('performanceTesting.websocket.command')} ${(row.command_index ?? -1) + 1} ${row.commandName || ''}` : t(`performanceTesting.websocket.stages.${row.stage === 'auth' ? 'auth' : 'heartbeat'}`) }}</span><span v-else-if="row.protocol === 'SSE'">SSE · {{ t('performanceTesting.sse.debugNotice') }}</span><span v-else-if="row.status">HTTP {{ row.status }}</span></div></template>
              <ul class="debug-rule-list">
                <li v-for="rule in row.assertions" :key="`assert-${rule.index}`">{{ t('performanceTesting.auth.assertion') }} #{{ rule.index + 1 }}：{{ K6_DEBUG_LABELS[rule.result] || '未提供诊断' }}</li>
                <li v-for="rule in row.extractors" :key="`extract-${rule.index}`">{{ t('performanceTesting.auth.extraction') }} #{{ rule.index + 1 }}：{{ K6_DEBUG_LABELS[rule.result] || '未提供诊断' }}</li>
                <li v-for="output in row.auth_outputs || []" :key="`auth-${output.role}`" class="bad-val">认证输出 · {{ { access_token: '访问 Token', expires_in: '有效秒数', cookie: 'Cookie 来源（Set-Cookie）' }[output.role] || '未知输出' }}<span v-if="output.index != null"> · {{ t('performanceTesting.auth.extraction') }} #{{ output.index + 1 }}</span>：{{ K6_DEBUG_LABELS[output.result] || '未提供诊断' }}</li>
              </ul>
              <p v-if="row.rules_truncated || row.response?.truncated" class="bad-val">{{ t('performanceTesting.auth.truncated') }}</p>
              <p v-if="row.response">{{ K6_DEBUG_LABELS[row.response.state] || '没有可用响应' }}</p>
              <pre v-if="row.response?.preview" class="debug-preview">{{ row.response.preview }}</pre>
            </el-collapse-item>
          </el-collapse>
        </el-card>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-card shadow="never">
              <template #header>{{ t('performanceTesting.sla.title') }}</template>
              <el-table v-if="(execution.sla_detail || []).length" :data="execution.sla_detail" size="small">
                <el-table-column label="范围" min-width="170"><template #default="{ row }">{{ slaScope(row) }}</template></el-table-column>
                <el-table-column prop="reason_label" label="原因" min-width="180" />
                <el-table-column prop="label" :label="t('performanceTesting.sla.result')" min-width="150">
                  <template #default="{ row }">{{ metricText(row.label) }}</template>
                </el-table-column>
                <el-table-column :label="t('performanceTesting.sla.threshold')" width="130" align="right"><template #default="{ row }">{{ row.comparator }} {{ row.threshold }} {{ row.unit }}</template></el-table-column>
                <el-table-column :label="t('performanceTesting.sla.actual')" width="100" align="right">
                  <template #default="{ row }">
                    <span :class="{ 'bad-val': row.passed === false }">{{ metricValue(row.actual) }}</span>
                  </template>
                </el-table-column>
                <el-table-column width="70" align="center">
                  <template #default="{ row }">
                    {{ slaResult(row) }}
                  </template>
                </el-table-column>
              </el-table>
              <el-empty v-else :description="t('performanceTesting.sla.notEvaluatedTip')" :image-size="60" />
            </el-card>
          </el-col>

          <el-col :span="12">
            <el-card shadow="never">
              <template #header>{{ isK6 ? '与基线对比（响应时间 / 吞吐量）' : t('performanceTesting.report.baselineCompare') }}</template>
              <p v-if="isK6" class="interface-note">仅比较响应时间与吞吐量的可比指标；不评估业务失败率或 SLA。业务失败率与 SLA 结果请查看对应指标。</p>
              <template v-if="baseline && baseline.has_baseline">
                <div class="baseline-meta">
                  {{ t('performanceTesting.comparison.baseline') }}: {{ baseline.baseline_execution_no }}
                  <el-tag
                    :type="baseline.degraded == null ? 'info' : baseline.degraded ? 'danger' : 'success'"
                    size="small"
                    effect="plain"
                  >
                    {{ baseline.degraded == null ? '未评估（无可比指标）' : baseline.degraded
                      ? (isK6 ? '已超退化阈值' : t('performanceTesting.report.worse'))
                      : (isK6 ? '未超退化阈值' : t('performanceTesting.report.better')) }}
                  </el-tag>
                </div>
                <el-alert v-if="baseline.reason" :title="baseline.reason" type="info" :closable="false" show-icon />
                <el-table :data="baseline.items" size="small">
                  <el-table-column prop="label" :label="t('performanceTesting.sla.result')" min-width="130">
                    <template #default="{ row }">{{ metricText(row.label) }}</template>
                  </el-table-column>
                  <el-table-column prop="baseline" :label="t('performanceTesting.comparison.baseline')" width="90" align="right" />
                  <el-table-column prop="current" :label="t('performanceTesting.sla.actual')" width="90" align="right" />
                  <el-table-column :label="t('performanceTesting.comparison.diff')" width="110" align="right">
                    <template #default="{ row }">
                      <span :class="row.degraded ? 'bad-val' : 'good-val'">
                        {{ row.change_pct > 0 ? '+' : '' }}{{ row.change_pct }}%
                      </span>
                    </template>
                  </el-table-column>
                </el-table>
              </template>
              <el-empty v-else :description="baseline?.reason || t('performanceTesting.report.noBaseline')" :image-size="60" />
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- ================= 时序图 ================= -->
      <el-tab-pane :label="t('performanceTesting.report.tabTimeline')" name="timeline">
        <el-alert v-if="hasProtocolStreams" type="info" :closable="false" :title="t('performanceTesting.sse.mixedRateNotice')" />
        <el-alert v-else-if="isK6" type="info" :closable="false" show-icon class="mb12"
          title="业务 RPS 曲线表示每次采样时最近已观测 UTC 完成秒桶的请求数，横轴是采样时间，并非对应运行秒的精确流量。迟到请求仅更新最终桶统计，不改写历史观测。旧记录缺少来源的窗口指标显示空白（未采集）；窗口没有业务请求时，响应时间和失败率也为空白，不代表 0。P95 为窗口直方图估算。" />
        <el-card shadow="never" class="chart-card">
          <template #header>{{ isK6 ? businessRateLabel : t('performanceTesting.report.chartTps') }} / {{ isK6 ? '脚本活动用户' : t('performanceTesting.report.chartUsers') }}</template>
          <div ref="tpsChartRef" class="chart-box" />
        </el-card>
        <el-card shadow="never" class="chart-card">
          <template #header>{{ hasProtocolStreams ? t('performanceTesting.websocket.mixedLatency') : t('performanceTesting.report.chartRt') }}</template>
          <div ref="rtChartRef" class="chart-box" />
        </el-card>
        <el-card shadow="never" class="chart-card">
          <template #header>{{ t('performanceTesting.report.chartError') }}</template>
          <div ref="errChartRef" class="chart-box chart-sm" />
        </el-card>
      </el-tab-pane>

      <!-- ================= 接口明细 ================= -->
      <el-tab-pane :label="t('performanceTesting.report.tabRequests')" name="requests">
        <el-card shadow="never">
          <el-table :data="isK6 ? k6InterfaceRows : requestStats" size="small" border>
            <el-table-column prop="step_name" :label="t('performanceTesting.editor.stepName')" min-width="180" fixed />
            <el-table-column v-if="isK6" label="API 地址" min-width="290">
              <template #default="{ row }"><code class="interface-path">{{ row.requestPath || '历史记录未保存地址' }}</code></template>
            </el-table-column>
            <el-table-column v-if="isK6" prop="stepId" label="步骤 ID" width="110" />
            <el-table-column v-if="isK6" label="阶段" min-width="160"><template #default="{ row }">{{ K6_PHASE_LABELS[row.phase] }}</template></el-table-column>
            <el-table-column prop="method" label="Method" width="80" />
            <el-table-column v-if="hasProtocolStreams" :label="t('performanceTesting.websocket.protocolLatency')" min-width="180"><template #default="{ row }">{{ t(protocolLatencyKey(row.protocol)) }}</template></el-table-column>
            <el-table-column prop="total" :label="hasProtocolStreams ? '已完成步骤' : isK6 ? '已完成请求' : t('performanceTesting.metric.totalRequests')" width="110" align="right" />
            <el-table-column prop="success" :label="t('performanceTesting.metric.successRequests')" width="90" align="right" />
            <el-table-column prop="failed" :label="t('performanceTesting.metric.failedRequests')" width="90" align="right" />
            <el-table-column v-if="isK6" label="失败原因与次数" min-width="290">
              <template #default="{ row }">{{ row.failureReason || '暂无统计' }}</template>
            </el-table-column>
            <el-table-column :label="t('performanceTesting.metric.errorRate')" width="100" align="right">
              <template #default="{ row }">
                <span :class="{ 'bad-val': row.error_rate > 0 }">{{ metricValue(row.error_rate) }}{{ row.error_rate == null ? '' : '%' }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="tps" :label="hasProtocolStreams ? businessRateLabel : isK6 ? '请求 RPS' : t('performanceTesting.metric.tps')" width="110" align="right" />
            <el-table-column prop="avg_rt" :label="hasProtocolStreams ? t('performanceTesting.websocket.mixedLatency') : t('performanceTesting.metric.avgRt')" width="100" align="right"><template #default="{ row }">{{ metricValue(row.avg_rt) }}</template></el-table-column>
            <el-table-column prop="min_rt" :label="t('performanceTesting.metric.minRt')" width="100" align="right"><template #default="{ row }">{{ metricValue(row.min_rt) }}</template></el-table-column>
            <el-table-column prop="p90_rt" :label="t('performanceTesting.metric.p90Rt')" width="90" align="right"><template #default="{ row }">{{ metricValue(row.p90_rt) }}</template></el-table-column>
            <el-table-column prop="p95_rt" :label="isK6 ? 'P95（估算）' : t('performanceTesting.metric.p95Rt')" width="110" align="right"><template #default="{ row }">{{ metricValue(row.p95_rt) }}</template></el-table-column>
            <el-table-column prop="p99_rt" :label="isK6 ? 'P99（估算）' : t('performanceTesting.metric.p99Rt')" width="110" align="right"><template #default="{ row }">{{ metricValue(row.p99_rt) }}</template></el-table-column>
            <el-table-column prop="max_rt" :label="t('performanceTesting.metric.maxRt')" width="100" align="right"><template #default="{ row }">{{ metricValue(row.max_rt) }}</template></el-table-column>
            <el-table-column :label="t('performanceTesting.metric.sentBytes')" width="100" align="right">
              <template #default="{ row }">{{ isK6 ? '未采集' : formatBytes(row.sent_bytes) }}</template>
            </el-table-column>
            <el-table-column :label="t('performanceTesting.metric.recvBytes')" width="100" align="right">
              <template #default="{ row }">{{ isK6 ? '未采集' : formatBytes(row.recv_bytes) }}</template>
            </el-table-column>
            <template #empty>
              <el-empty :description="t('performanceTesting.common.empty')" />
            </template>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- ================= 错误分析 ================= -->
      <el-tab-pane :label="t('performanceTesting.report.tabErrors')" name="errors">
        <el-card shadow="never">
          <template #header>{{ t('performanceTesting.report.errorTop') }}</template>
          <el-table v-if="errorRows.length" :data="errorRows" size="small" border>
            <el-table-column prop="step_name" :label="t('performanceTesting.report.errorStep')" width="180" />
            <el-table-column prop="type" :label="t('performanceTesting.report.errorType')" width="180" />
            <el-table-column prop="message" :label="t('performanceTesting.report.errorMessage')" min-width="320" show-overflow-tooltip />
            <el-table-column prop="count" :label="t('performanceTesting.report.errorCount')" width="110" align="right" />
          </el-table>
          <el-empty v-else :description="t('performanceTesting.report.noErrors')" :image-size="80" />
        </el-card>
      </el-tab-pane>

      <!-- ================= 执行配置 ================= -->
      <el-tab-pane :label="t('performanceTesting.report.tabConfig')" name="config">
        <ExecutionEvidence v-if="isK6" :execution="execution" />
        <el-alert
          type="info"
          :closable="false"
          show-icon
          :title="t('performanceTesting.report.snapshotTip')"
          class="snapshot-tip"
        />
        <el-row :gutter="12">
          <el-col :span="10">
            <el-card shadow="never">
              <template #header>{{ t('performanceTesting.report.loadSnapshot') }}</template>
              <el-descriptions :column="1" border size="small">
                <el-descriptions-item v-if="isK6" label="压测引擎">k6</el-descriptions-item>
                <el-descriptions-item :label="t('performanceTesting.loadModel.label')">
                  {{ loadModelLabel }}
                </el-descriptions-item>
                <el-descriptions-item
                  v-for="(v, k) in displayLoadSnapshot"
                  :key="k"
                  :label="loadFieldLabel(k)"
                >
                  {{ typeof v === 'object' ? JSON.stringify(v) : v }}
                </el-descriptions-item>
              </el-descriptions>
              <el-descriptions :column="1" border size="small" class="mt12">
                <el-descriptions-item :label="t('performanceTesting.execution.executedBy')">
                  {{ execution.executed_by?.username || '-' }}
                </el-descriptions-item>
                <el-descriptions-item :label="t('performanceTesting.execution.triggerType')">
                  {{ execution.trigger_type ? t('performanceTesting.trigger.' + execution.trigger_type) : '-' }}
                </el-descriptions-item>
                <el-descriptions-item :label="t('performanceTesting.execution.startTime')">
                  {{ formatTime(execution.start_time) }}
                </el-descriptions-item>
                <el-descriptions-item :label="t('performanceTesting.execution.endTime')">
                  {{ formatTime(execution.end_time) }}
                </el-descriptions-item>
                <el-descriptions-item :label="t('performanceTesting.metric.duration')">
                  {{ execution.duration ? formatDuration(execution.duration) : '-' }}
                </el-descriptions-item>
                <el-descriptions-item label="Worker">{{ execution.worker_host || '-' }}</el-descriptions-item>
              </el-descriptions>
            </el-card>
          </el-col>
          <el-col :span="14">
            <el-card shadow="never">
              <template #header>{{ t('performanceTesting.report.stepsSnapshot') }}</template>
              <el-table :data="execution.steps_snapshot || []" size="small" border>
                <el-table-column type="index" width="50" />
                <el-table-column prop="name" :label="t('performanceTesting.editor.stepName')" min-width="150" />
                <el-table-column prop="method" label="Method" width="80" />
            <el-table-column v-if="hasProtocolStreams" :label="t('performanceTesting.websocket.protocolLatency')" min-width="180"><template #default="{ row }">{{ t(protocolLatencyKey(row.protocol)) }}</template></el-table-column>
                <el-table-column v-if="!isK6" prop="url" :label="t('performanceTesting.editor.url')" min-width="220" show-overflow-tooltip />
                <el-table-column :label="t('performanceTesting.editor.stepSetup')" width="80" align="center">
                  <template #default="{ row }">
                    <el-tag v-if="row.is_setup" size="small" type="warning" effect="plain">
                      {{ t('performanceTesting.common.yes') }}
                    </el-tag>
                    <span v-else>-</span>
                  </template>
                </el-table-column>
                <el-table-column :label="t('performanceTesting.editor.stepEnabled')" width="150" align="center">
                  <template #default="{ row }">
                    {{ isK6 && ['login', 'refresh'].includes(row.auth_phase) ? '按认证策略调用' : row.enabled === true ? t('performanceTesting.common.yes') : row.enabled === false ? t('performanceTesting.common.no') : '未记录' }}
                  </template>
                </el-table-column>
              </el-table>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- AI 分析 Tab（懒加载，点击才请求） -->
      <el-tab-pane :label="t('performanceTesting.report.tabAiAnalysis')" name="ai-analysis">
        <div class="ai-panel">
          <!-- 未开始：引导卡片 -->
          <div v-if="!aiAnalysis.loaded && !aiAnalysis.loading && !aiAnalysis.text && !aiAnalysis.error" class="ai-hero">
            <div class="ai-hero-icon">
              <el-icon :size="30"><MagicStick /></el-icon>
            </div>
            <h3 class="ai-hero-title">{{ t('performanceTesting.report.tabAiAnalysis') }}</h3>
            <p class="ai-hero-desc">{{ t('performanceTesting.report.aiDesc') }}</p>
            <div class="ai-hero-features">
              <span class="ai-feature">{{ t('performanceTesting.report.aiFeatureBottleneck') }}</span>
              <span class="ai-feature">{{ t('performanceTesting.report.aiFeatureSuggestion') }}</span>
              <span class="ai-feature">{{ t('performanceTesting.report.aiFeatureRisk') }}</span>
            </div>
            <el-button type="primary" round size="large" class="ai-start-btn" :disabled="!reportReady" @click="loadAiAnalysis">
              <el-icon><MagicStick /></el-icon>
              {{ t('performanceTesting.report.aiStart') }}
            </el-button>
          </div>

          <!-- 分析中 -->
          <div v-if="aiAnalysis.loading && !aiAnalysis.text" class="ai-streaming-hint">
            <el-icon class="is-loading"><Loading /></el-icon>
            <span>{{ t('performanceTesting.report.aiStreaming') }}</span>
            <span class="ai-dots"><i>.</i><i>.</i><i>.</i></span>
          </div>

          <!-- 分析内容（Markdown 渲染） -->
          <div v-if="aiAnalysis.text" class="ai-card">
            <div class="ai-card-head">
              <div class="ai-card-title">
                <span class="ai-badge">
                  <el-icon><MagicStick /></el-icon>
                  {{ t('performanceTesting.report.aiResultTitle') }}
                </span>
                <span v-if="aiAnalysis.loading" class="ai-streaming-tag">
                  <el-icon class="is-loading"><Loading /></el-icon>
                  {{ t('performanceTesting.report.aiGenerating') }}
                </span>
              </div>
              <div class="ai-card-actions">
                <el-button link size="small" :icon="DocumentCopy" :disabled="aiAnalysis.loading" @click="copyAiText">
                  {{ t('performanceTesting.report.aiCopy') }}
                </el-button>
                <el-button link size="small" type="primary" :icon="Refresh" :disabled="!reportReady || aiAnalysis.loading" @click="loadAiAnalysis">
                  {{ t('performanceTesting.report.aiReanalyze') }}
                </el-button>
              </div>
            </div>
            <div class="ai-card-body markdown-body" v-html="aiRenderedHtml"></div>
          </div>

          <div v-if="aiAnalysis.error" class="ai-error">
            <el-alert :title="aiAnalysis.error" type="error" show-icon :closable="false" />
            <el-button class="ai-retry-btn" type="primary" plain size="small" :disabled="!reportReady" @click="loadAiAnalysis">
              {{ t('performanceTesting.common.retry') }}
            </el-button>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- ---------- 分享直链对话框 ---------- -->
    <el-dialog
      v-model="shareVisible"
      :title="t('performanceTesting.share.title')"
      width="560px"
      append-to-body
      @close="onShareClosed"
    >
      <el-alert
        :title="t('performanceTesting.share.tip')"
        type="warning"
        :closable="false"
        show-icon
        class="share-tip"
      />
      <el-form label-width="96px" class="share-form">
        <el-form-item :label="t('performanceTesting.share.expiry')">
          <el-radio-group v-model="shareExpiry">
            <el-radio-button :value="0">{{ t('performanceTesting.share.never') }}</el-radio-button>
            <el-radio-button :value="1">{{ t('performanceTesting.share.day1') }}</el-radio-button>
            <el-radio-button :value="7">{{ t('performanceTesting.share.day7') }}</el-radio-button>
            <el-radio-button :value="30">{{ t('performanceTesting.share.day30') }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
      </el-form>

      <template v-if="shareUrl">
        <div class="share-link-block">
          <div class="share-label">{{ t('performanceTesting.share.reportLink') }}</div>
          <el-input :model-value="shareUrl" readonly>
            <template #append>
              <el-button :icon="DocumentCopy" @click="copyText(shareUrl)">
                {{ t('performanceTesting.share.copy') }}
              </el-button>
            </template>
          </el-input>
          <div class="share-label">{{ t('performanceTesting.share.rawLink') }}</div>
          <el-input :model-value="shareRawUrl" readonly>
            <template #append>
              <el-button :icon="DocumentCopy" @click="copyText(shareRawUrl)">
                {{ t('performanceTesting.share.copy') }}
              </el-button>
            </template>
          </el-input>
          <div v-if="shareExpiresAt" class="share-exp">
            {{ t('performanceTesting.share.expiresAt') }}: {{ formatTime(shareExpiresAt) }}
          </div>
        </div>
        <div class="share-actions">
          <el-button type="danger" plain :icon="CircleClose" @click="handleRevoke">
            {{ t('performanceTesting.share.revoke') }}
          </el-button>
        </div>
      </template>
      <template v-else>
        <div class="share-actions">
          <el-button type="primary" :loading="shareGenerating" :icon="Share" @click="handleGenerate">
            {{ t('performanceTesting.share.generate') }}
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import ExecutionEvidence from './components/ExecutionEvidence.vue'
import { observedMetrics, metricValue, slaScope, slaResult, SLA_REASONS } from './slaReport.mjs'
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Refresh, Link, Download, Share, DocumentCopy, CircleClose, MagicStick, Loading } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import { nullableLineOptions } from './nullableLine.mjs'
import { marked } from 'marked'

import MetricCards from './components/MetricCards.vue'
import WebSocketMetrics from './components/WebSocketMetrics.vue'
import { hasWebsocketSteps } from './websocketMetrics.mjs'
import { hasSseSteps, protocolLatencyKey } from './sseMetrics.mjs'
import SseMetrics from './components/SseMetrics.vue'
import ReminderRecoveryStatus from './components/ReminderRecoveryStatus.vue'
import ExecutionPolicyMetrics from './components/ExecutionPolicyMetrics.vue'
import NativeVUObservations from './components/NativeVUObservations.vue'
import { nativeVuFromSamples } from './nativeVuObservations.mjs'
import { statusTagType, slaTagType, formatTime, formatDuration, apiError } from './shared'
import { buildK6InterfaceRows, formatK6Count, buildK6DebugRows, K6_PHASE_LABELS, K6_DEBUG_LABELS } from './k6InterfaceStats.mjs'
import request from '@/utils/api'
import { useUserStore } from '@/stores/user'
import {
  getPerfExecution, getPerfSamples, getPerfRequestStats, generatePerfReport,
  compareWithBaseline, generatePerfShareLink, revokePerfShareLink
} from '@/api/performance-testing'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const executionId = computed(() => Number(route.params.id))
const loading = ref(false)
const loadError = ref('')
const regenerating = ref(false)
const recoveryLoading = ref(false)
const activeTab = ref('overview')
let reportGeneration = 0
let disposed = false
const pendingHtmlWindows = new Set()

const emptyExecution = () => ({
  execution_no: '', scenario_name: '', status: '', sla_result: '', summary: {},
  sla_detail: [], load_snapshot: {}, steps_snapshot: [], report_url: '',
  has_raw_detail: false, executed_by: null,
  verdict: 'NOT_EVALUATED', verdict_details: [], resource_recovery: null
})
const execution = reactive(emptyExecution())
const reportReady = computed(() => !disposed && !loading.value && !loadError.value
  && Number.isSafeInteger(executionId.value) && executionId.value > 0 && Number(execution.id) === executionId.value)
const htmlDownloadReady = computed(() => reportReady.value && !!execution.report_url
  && ['COMPLETED', 'FAILED', 'STOPPED', 'TIMEOUT'].includes(execution.status))

function ownsReport(owner) {
  return !disposed && owner.generation === reportGeneration && Object.is(owner.id, executionId.value)
}
function currentReport() {
  if (disposed || !reportReady.value) return null
  return { id: executionId.value, generation: reportGeneration, filename: execution.execution_no || `perf-${executionId.value}` }
}
async function refreshRecovery() {
  const owner = currentReport()
  if (!owner || recoveryLoading.value) return
  recoveryLoading.value = true
  try {
    const { data } = await getPerfExecution(owner.id)
    if (!ownsReport(owner)) return
    if (Number(data?.id) !== owner.id) throw new Error('执行 ID 与当前报告不一致')
    execution.resource_recovery = data.resource_recovery ?? null
  } catch (error) {
    if (ownsReport(owner)) { execution.resource_recovery = null; ElMessage.error(apiError(error, t('performanceTesting.common.empty'))) }
  } finally {
    if (ownsReport(owner)) recoveryLoading.value = false
  }
}
const hasWebsocket = computed(() => hasWebsocketSteps(execution.steps_snapshot || []) || Boolean(execution.summary?.websocket))
const hasSse = computed(() => hasSseSteps(execution.steps_snapshot || []) || Boolean(execution.summary?.sse))
const hasProtocolStreams = computed(() => hasWebsocket.value || hasSse.value)
const businessRateLabel = computed(() => hasProtocolStreams.value ? t('performanceTesting.websocket.mixedRate') : '业务 RPS')
const isK6 = computed(() => execution.load_snapshot?._engine === 'K6'
  || (!execution.load_snapshot?._engine && execution.summary?.http_total != null
    && execution.summary?.business_total != null))
const k6DebugRows = computed(() => buildK6DebugRows(execution.summary?.debug_details, execution.steps_snapshot || []))
const rateLabel = computed(() => isK6.value ? businessRateLabel.value : 'TPS')
const cpuDescription = computed(() => {
  const description = execution.summary?.metric_semantics?.cpu_percent
  return typeof description === 'string' && description.trim() ? description.trim() : '发压资源见指标口径'
})
const k6RequestCards = computed(() => [
  ['http_started', '已发起 HTTP 请求'], ['http_total', '已完成 HTTP 请求（含登录与刷新）'],
  ['http_incomplete', '未完成 HTTP 请求'], ['failed_requests', '业务失败请求数'],
  ['business_started', '已发起业务请求'], ['business_total', '已完成业务请求'],
  ['business_incomplete', '未完成业务请求'], ['completed_iterations', '业务完成轮次'],
  ['executor_iterations', '执行器完成轮次'], ['idle_iterations', '空轮次']
].map(([key, label]) => ({
  key, label: hasProtocolStreams.value && (key.startsWith('business_') || key === 'failed_requests') ? label.replace('请求', '步骤') : label, value: execution.summary?.[key] ?? '未采集',
  status: key.endsWith('_incomplete') && execution.summary?.[key] > 0 ? 'warn' : 'normal'
})))

// AI 分析状态（懒加载，点击 Tab 才请求）
const aiAnalysis = reactive({
  loaded: false,
  loading: false,
  text: '',
  error: ''
})
const aiAbortCtrl = ref(null)
let aiIdleTimer = null

// AI 分析输出为 markdown（marked 默认转义原始 HTML，防 XSS）；
// 流式输出时对未闭合语法标记做临时清理，避免打字机过程中出现裸符号
const aiRenderedHtml = computed(() => {
  const text = aiAnalysis.text || ''
  if (!text) return ''
  let src = text
  if (aiAnalysis.loading) {
    const boldCount = (src.match(/\*\*/g) || []).length
    if (boldCount % 2 === 1) src += '**'
    if ((src.match(/^>/gm) || []).length && !/\n\s*$/.test(src)) src += '\n'
  }
  let html = marked.parse(src)
  if (aiAnalysis.loading) {
    html += '<span class="ai-streaming-cursor"></span>'
  }
  return html
})

function copyAiText() {
  navigator.clipboard?.writeText(aiAnalysis.text).then(
    () => ElMessage.success(t('performanceTesting.share.copied')),
    () => ElMessage.warning(t('performanceTesting.share.copyFailed'))
  )
}
const samples = ref([])
const nativeObservations = computed(() => nativeVuFromSamples(samples.value, executionId.value))
const requestStats = ref([])
const requestStatsFailed = ref(false)
const reportIsActive = computed(() => ['PENDING', 'PREPARING', 'RUNNING', 'STOPPING'].includes(execution.status))
const k6InterfaceRows = computed(() => buildK6InterfaceRows({
  stats: execution.summary?.step_metrics || requestStats.value, steps: execution.steps_snapshot || [],
  errorTop: execution.summary?.error_top || [], live: reportIsActive.value
}))
const interfaceStatsNotice = computed(() => {
  if (requestStatsFailed.value) return '接口明细读取失败，请刷新页面重试；暂无数据不代表 0 次。'
  if (reportIsActive.value) return '任务仍在运行，本页不实时刷新；接口次数与原因将在结束后汇总，请前往实时监控查看累计采样。'
  return '本次执行的结束后汇总：每行对应一个接口，成功和失败分别计数。'
})
const baseline = ref(null)

const tpsChartRef = ref(null)
const rtChartRef = ref(null)
const errChartRef = ref(null)
let tpsChart = null
let rtChart = null
let errChart = null

function num(v, digits = 1) {
  return metricValue(v, digits)
}

function formatBytes(bytes) {
  const b = Number(bytes) || 0
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  if (b < 1024 * 1024 * 1024) return `${(b / 1024 / 1024).toFixed(1)} MB`
  return `${(b / 1024 / 1024 / 1024).toFixed(2)} GB`
}

const overviewCards = computed(() => {
  const s = observedMetrics(execution.summary || {}, isK6.value)
  const errRate = s.error_rate
  return [
    { label: isK6.value ? (hasProtocolStreams.value ? t('performanceTesting.websocket.mixedCompleted') : '已完成业务请求') : t('performanceTesting.metric.totalRequests'), value: s.business_total ?? s.total_requests ?? '-', status: 'normal' },
    { label: isK6.value ? (hasProtocolStreams.value ? `平均${businessRateLabel.value}` : '平均业务 RPS') : t('performanceTesting.metric.tps'), value: num(s.business_rps ?? s.tps), status: 'normal' },
    { label: isK6.value ? (hasProtocolStreams.value ? `峰值${businessRateLabel.value}（固定1秒桶）` : '峰值业务 RPS（固定1秒桶）') : t('performanceTesting.metric.peakTps'), value: num(s.peak_tps), status: 'normal' },
    { label: isK6.value ? (hasProtocolStreams.value ? t('performanceTesting.websocket.mixedLatency') : '平均业务响应') : t('performanceTesting.metric.avgRt'), value: num(s.avg_rt), unit: 'ms', status: 'normal' },
    { label: isK6.value ? '累计 P95（估算）' : t('performanceTesting.metric.p95Rt'), value: num(s.p95_rt), unit: 'ms', status: 'normal' },
    {
      label: isK6.value ? '业务失败率' : t('performanceTesting.metric.errorRate'),
      value: num(errRate, 2),
      unit: '%',
      status: errRate == null ? 'normal' : errRate > 1 ? 'bad' : (errRate > 0 ? 'warn' : 'good')
    }
  ]
})

const loadModelLabel = computed(() => {
  const model = (execution.load_snapshot || {}).model
  return model ? t('performanceTesting.loadModel.' + model) : '-'
})

const displayLoadSnapshot = computed(() => {
  const snap = { ...(execution.load_snapshot || {}) }
  delete snap.model
  // 内部字段不展示给用户
  Object.keys(snap).forEach(k => {
    if (k.startsWith('_') || (isK6.value && !['concurrency', 'duration', 'iterations_per_vu'].includes(k))
      || snap[k] === 0 || snap[k] === '' || snap[k] == null) delete snap[k]
  })
  return snap
})

function loadFieldLabel(key) {
  if (!isK6.value) return key
  return {
    concurrency: '配置 VU',
    duration: Number(execution.load_snapshot?.iterations_per_vu) > 0 ? '最长运行时间（秒）' : '持续时间（秒）',
    iterations_per_vu: '每用户轮次'
  }[key] || key
}

function metricText(value) {
  return isK6.value ? String(value ?? '').replace(/TPS/g, businessRateLabel.value) : value
}

// 错误明细：从各步骤的 error_detail 聚合，按出现次数倒序取 TOP10
const errorRows = computed(() => {
  if (isK6.value) return k6InterfaceRows.value.filter(row => row.failed > 0).map(row => ({
    step_name: `${row.name} · #${row.stepId}`, type: '请求失败', message: row.failureReason, count: row.failed
  }))
  const rows = []
  requestStats.value.forEach(stat => {
    (stat.error_detail || []).forEach(item => {
      rows.push({
        step_name: stat.step_name,
        type: item.type || item.error_type || 'Unknown',
        message: item.message || item.error || '',
        count: item.count || 1
      })
    })
  })
  return rows.sort((a, b) => b.count - a.count).slice(0, 10)
})

// ------------------------------------------------------------------ //
// 图表
// ------------------------------------------------------------------ //
const AXIS_BASE = {
  grid: { left: 50, right: 50, top: 40, bottom: 40 },
  tooltip: { trigger: 'axis' },
  legend: { top: 6 }
}

function xAxisData() {
  return samples.value.map(s => `${Math.round(s.ts_offset)}s`)
}

function renderCharts() {
  const x = xAxisData()

  if (tpsChartRef.value) {
    tpsChart = tpsChart || echarts.init(tpsChartRef.value)
    tpsChart.setOption({
      ...AXIS_BASE,
      xAxis: { type: 'category', data: x, boundaryGap: false },
      yAxis: [
        { type: 'value', name: rateLabel.value },
        { type: 'value', name: isK6.value ? '脚本活动用户' : t('performanceTesting.metric.activeUsers') }
      ],
      series: [
        {
          name: rateLabel.value,
          type: 'line',
          smooth: true,
          showSymbol: false,
          ...nullableLineOptions(samples.value.map(s => s.tps), isK6.value),
          itemStyle: { color: '#1890ff' },
          areaStyle: { opacity: 0.12 }
        },
        {
          name: isK6.value ? '脚本活动用户' : t('performanceTesting.metric.activeUsers'),
          type: 'line',
          yAxisIndex: 1,
          step: 'end',
          showSymbol: false,
          data: samples.value.map(s => s.active_users),
          itemStyle: { color: '#722ed1' },
          lineStyle: { type: 'dashed' }
        }
      ]
    })
  }

  if (rtChartRef.value) {
    rtChart = rtChart || echarts.init(rtChartRef.value)
    rtChart.setOption({
      ...AXIS_BASE,
      xAxis: { type: 'category', data: x, boundaryGap: false },
      yAxis: { type: 'value', name: 'ms' },
      series: [
        { name: 'avg', type: 'line', smooth: true, showSymbol: false, ...nullableLineOptions(samples.value.map(s => observedMetrics(s, isK6.value).avg_rt), isK6.value), itemStyle: { color: '#52c41a' } },
        { name: 'P90', type: 'line', smooth: true, showSymbol: false, ...nullableLineOptions(samples.value.map(s => s.p90_rt), isK6.value), itemStyle: { color: '#faad14' } },
        { name: 'P95', type: 'line', smooth: true, showSymbol: false, ...nullableLineOptions(samples.value.map(s => observedMetrics(s, isK6.value).p95_rt), isK6.value), itemStyle: { color: '#fa8c16' } },
        { name: 'P99', type: 'line', smooth: true, showSymbol: false, ...nullableLineOptions(samples.value.map(s => observedMetrics(s, isK6.value).p99_rt), isK6.value), itemStyle: { color: '#f5222d' } }
      ]
    })
  }

  if (errChartRef.value) {
    errChart = errChart || echarts.init(errChartRef.value)
    errChart.setOption({
      ...AXIS_BASE,
      legend: { show: false },
      grid: { left: 50, right: 50, top: 20, bottom: 40 },
      xAxis: { type: 'category', data: x, boundaryGap: false },
      yAxis: { type: 'value', name: '%', max: (v) => Math.max(1, Math.ceil(v.max)) },
      series: [{
        name: t('performanceTesting.metric.errorRate'),
        type: 'line',
        step: 'end',
        showSymbol: false,
        ...nullableLineOptions(samples.value.map(s => observedMetrics(s, isK6.value).error_rate), isK6.value),
        itemStyle: { color: '#f5222d' },
        areaStyle: { opacity: 0.15 }
      }]
    })
  }
}

function resizeCharts() {
  tpsChart?.resize()
  rtChart?.resize()
  errChart?.resize()
}

async function onTabChange(name) {
  if (name === 'timeline') {
    await nextTick()
    if (disposed) return
    renderCharts()
    resizeCharts()
  }
  if (name === 'ai-analysis' && !aiAnalysis.loaded && !aiAnalysis.loading) {
    loadAiAnalysis()
  }
}

// AI 分析：fetch + ReadableStream 消费 SSE（可携带 JWT 认证头），打字机效果
async function loadAiAnalysis() {
  const owner = currentReport()
  if (!owner) return
  clearTimeout(aiIdleTimer)
  aiAbortCtrl.value?.abort()
  aiAnalysis.loading = true
  aiAnalysis.loaded = false
  aiAnalysis.text = ''
  aiAnalysis.error = ''

  const userStore = useUserStore()
  const ctrl = new AbortController()
  aiAbortCtrl.value = ctrl
  const isCurrent = () => ownsReport(owner) && aiAbortCtrl.value === ctrl && !ctrl.signal.aborted
  let reader
  let idleTimer

  // 空闲超时：每收到一个数据块重置，流式输出期间不会误杀
  const resetIdleTimer = () => {
    clearTimeout(idleTimer)
    idleTimer = setTimeout(() => {
      if (isCurrent() && aiAnalysis.loading) {
        aiAnalysis.error = t('performanceTesting.report.aiTimeout')
        aiAnalysis.loading = false
        ctrl.abort()
      }
    }, 60000)
    aiIdleTimer = idleTimer
  }
  const finish = () => {
    clearTimeout(idleTimer)
    if (!isCurrent()) return
    aiAnalysis.loaded = true
    aiAnalysis.loading = false
  }
  const fail = (msg) => {
    clearTimeout(idleTimer)
    if (!isCurrent()) return
    if (aiAnalysis.loading) {
      aiAnalysis.error = msg || t('performanceTesting.report.aiFailed')
      aiAnalysis.loading = false
    }
  }

  resetIdleTimer()
  try {
    const resp = await fetch(`/api/perf-testing/executions/${owner.id}/ai-analysis/`, {
      method: 'GET',
      signal: ctrl.signal,
      credentials: 'include',
      headers: {
        Accept: 'text/event-stream, application/json',
        ...(userStore.accessToken ? { Authorization: `Bearer ${userStore.accessToken}` } : {})
      }
    })
    if (!isCurrent()) return

    if (!resp.ok) {
      let detail = ''
      try {
        const body = await resp.json()
        detail = body.detail || body.error || ''
      } catch (e) { /* 响应体非 JSON，忽略 */ }
      if (resp.status === 401) fail(t('performanceTesting.report.aiAuthExpired'))
      else if (resp.status === 403) fail(t('performanceTesting.report.aiForbidden'))
      else fail(detail || t('performanceTesting.report.aiHttpFailed', { status: resp.status }))
      return
    }

    const contentType = resp.headers.get('Content-Type') || ''
    // Redis 缓存命中 → 一次性 JSON 返回（< 100ms）
    if (contentType.includes('application/json')) {
      const data = await resp.json()
      if (!isCurrent()) return
      if (data.error) {
        fail(data.error)
      } else {
        aiAnalysis.text = data.analysis || ''
        finish()
      }
      return
    }

    // SSE 流式：按 "\n\n" 切事件，逐行解析 data: 前缀
    reader = resp.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (!isCurrent()) return
      if (done) break
      resetIdleTimer()
      buffer += decoder.decode(value, { stream: true })

      let sep
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const rawEvent = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)
        for (const line of rawEvent.split('\n')) {
          if (!line.startsWith('data: ')) continue
          try {
            const data = JSON.parse(line.slice(6))
            if (data.chunk) aiAnalysis.text += data.chunk
            if (data.error) { fail(data.error); return }
            if (data.done) { finish(); return }
          } catch (e) { /* 单行解析失败忽略，等待后续数据 */ }
        }
      }
    }
    // 流正常结束但未收到 done 事件（兜底视为完成）
    if (aiAnalysis.loading) finish()
  } catch (e) {
    if (e.name !== 'AbortError') fail()
  } finally {
    clearTimeout(idleTimer)
    if (reader) {
      try { await reader.cancel() } catch (e) { /* 连接可能已由 AbortController 关闭 */ }
      reader.releaseLock()
    }
  }
}

// ------------------------------------------------------------------ //
// 数据加载
// ------------------------------------------------------------------ //
async function loadAll() {
  if (disposed) return
  const owner = { id: executionId.value, generation: ++reportGeneration }
  loading.value = true
  loadError.value = ''
  regenerating.value = false
  recoveryLoading.value = false
  Object.keys(execution).forEach(key => delete execution[key])
  Object.assign(execution, emptyExecution())
  samples.value = []
  requestStats.value = []
  requestStatsFailed.value = false
  baseline.value = null
  ;[tpsChart, rtChart, errChart].forEach(chart => chart?.clear())
  clearTimeout(aiIdleTimer)
  aiAbortCtrl.value?.abort()
  aiAbortCtrl.value = null
  Object.assign(aiAnalysis, { loaded: false, loading: false, text: '', error: '' })
  shareVisible.value = false
  onShareClosed()
  pendingHtmlWindows.forEach(w => w.close())
  pendingHtmlWindows.clear()
  let statsFailed = false
  try {
    if (!Number.isSafeInteger(owner.id) || owner.id <= 0) throw new Error('无效的执行 ID')
    const [detailRes, sampleRes, statsRes] = await Promise.all([
      getPerfExecution(owner.id),
      getPerfSamples(owner.id, 1000).catch(() => ({ data: { samples: [] } })),
      getPerfRequestStats(owner.id).catch(() => {
        statsFailed = true
        return { data: [] }
      })
    ])
    if (!ownsReport(owner)) return
    if (Number(detailRes.data?.id) !== owner.id) throw new Error('报告执行 ID 与当前页面不一致，请重试')
    let comparison
    try {
      const cmp = await compareWithBaseline({ execution_id: owner.id })
      comparison = cmp.data
    } catch (e) {
      comparison = { has_baseline: false }
    }
    if (!ownsReport(owner)) return
    Object.assign(execution, detailRes.data)
    samples.value = sampleRes.data?.samples || []
    requestStats.value = statsRes.data || []
    requestStatsFailed.value = statsFailed
    baseline.value = comparison
    if (!execution.report_url && !['PENDING', 'PREPARING', 'RUNNING', 'STOPPING'].includes(execution.status)) {
      ElMessage.info(t('performanceTesting.report.notReady'))
    }
  } catch (e) {
    if (ownsReport(owner)) loadError.value = apiError(e, t('performanceTesting.common.empty'))
  } finally {
    if (ownsReport(owner)) {
      loading.value = false
      await nextTick()
      if (ownsReport(owner) && reportReady.value) onTabChange(activeTab.value)
    }
  }
}

async function handleRegenerate() {
  const owner = currentReport()
  if (!owner || regenerating.value) return
  regenerating.value = true
  try {
    const res = await generatePerfReport(owner.id)
    if (!ownsReport(owner)) return
    execution.report_url = res.data?.report_url
    ElMessage.success(t('performanceTesting.report.regenerated'))
  } catch (e) {
    if (ownsReport(owner)) ElMessage.error(apiError(e, t('performanceTesting.report.regenerate')))
  } finally {
    if (ownsReport(owner)) regenerating.value = false
  }
}

// 报告接口需鉴权，不能用 window.open 直链（浏览器新窗口不带 token，会 401）。
// 用带 token 的 axios 拉取：预览注入新窗口，导出与原始数据用 blob 下载。
function openHtml() {
  const owner = currentReport()
  if (!owner || !execution.report_url) return
  const w = window.open('', '_blank')
  if (!w) {
    ElMessage.error(t('performanceTesting.report.popupBlocked'))
    return
  }
  pendingHtmlWindows.add(w)
  w.document.title = t('performanceTesting.report.title')
  request({ url: `/perf-testing/executions/${owner.id}/report/`, responseType: 'text' })
    .then(res => {
      if (!ownsReport(owner) || w.closed) { w.close(); return }
      w.document.open()
      w.document.write(typeof res.data === 'string' ? res.data : '')
      w.document.close()
    })
    .catch(() => {
      w.close()
      if (ownsReport(owner)) ElMessage.error(t('performanceTesting.report.openFailed'))
    })
    .finally(() => pendingHtmlWindows.delete(w))
}
async function exportReport(format) {
  const owner = currentReport()
  if (!owner || reportIsActive.value || (format === 'html' && !htmlDownloadReady.value)) return
  try {
    const response = await request({ url: `/perf-testing/executions/${owner.id}/report/`, responseType: 'blob',
      ...(format === 'html' ? { method: 'get' } : { params: { export: format } }) })
    if (!ownsReport(owner)) return
    const url = URL.createObjectURL(response.data)
    try {
      const link = document.createElement('a')
      link.href = url; link.download = `${owner.filename}.${format}`; link.click()
    } finally {
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    }
  } catch (error) { if (ownsReport(owner)) ElMessage.error(apiError(error, '导出失败')) }
}

function downloadRaw() {
  const owner = currentReport()
  if (!owner || !execution.has_raw_detail) return
  request({ url: `/perf-testing/executions/${owner.id}/download-raw/`, responseType: 'blob' })
    .then(res => {
      if (!ownsReport(owner)) return
      const url = URL.createObjectURL(res.data)
      const a = document.createElement('a')
      a.href = url
      a.download = `${owner.filename}_raw.csv.gz`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    })
    .catch(() => {
      if (ownsReport(owner)) ElMessage.error(t('performanceTesting.report.downloadFailed'))
    })
}
function goBack() {
  router.push('/performance-testing/executions')
}

// ---------- 分享直链 ----------
const shareVisible = ref(false)
const shareGenerating = ref(false)
const shareExpiry = ref(7)
const shareUrl = ref('')
const shareRawUrl = ref('')
const shareExpiresAt = ref('')
const shareToken = ref('')
let shareGeneration = 0

function openShare() {
  if (!currentReport() || !execution.report_url) return
  // 打开即按当前 expiry 预生成一条分享直链
  shareVisible.value = true
  handleGenerate()
}
async function handleGenerate() {
  const owner = currentReport()
  if (!owner || !shareVisible.value) return
  const generation = ++shareGeneration
  const isCurrent = () => ownsReport(owner) && generation === shareGeneration && shareVisible.value
  shareGenerating.value = true
  try {
    const res = await generatePerfShareLink(
      owner.id, shareExpiry.value > 0 ? shareExpiry.value : null)
    if (!isCurrent()) return
    shareToken.value = res.data?.token || ''
    shareUrl.value = res.data?.share_url || ''
    shareRawUrl.value = res.data?.raw_url || ''
    shareExpiresAt.value = res.data?.expires_at || ''
    ElMessage.success(t('performanceTesting.share.generated'))
  } catch (e) {
    if (isCurrent()) ElMessage.error(apiError(e, t('performanceTesting.share.generate')))
  } finally {
    if (isCurrent()) shareGenerating.value = false
  }
}
async function handleRevoke() {
  const owner = currentReport()
  if (!owner || !shareVisible.value) return
  const generation = ++shareGeneration
  const isCurrent = () => ownsReport(owner) && generation === shareGeneration && shareVisible.value
  shareGenerating.value = false
  try {
    await revokePerfShareLink(owner.id)
    if (!isCurrent()) return
    shareUrl.value = ''
    shareRawUrl.value = ''
    shareExpiresAt.value = ''
    shareToken.value = ''
    ElMessage.success(t('performanceTesting.share.revoked'))
  } catch (e) {
    if (isCurrent()) ElMessage.error(apiError(e, t('performanceTesting.share.revoke')))
  }
}
function copyText(text) {
  navigator.clipboard?.writeText(text).then(
    () => ElMessage.success(t('performanceTesting.share.copied')),
    () => ElMessage.warning(t('performanceTesting.share.copyFailed'))
  )
}
function onShareClosed() {
  ++shareGeneration
  shareGenerating.value = false
  shareUrl.value = ''
  shareRawUrl.value = ''
  shareExpiresAt.value = ''
  shareToken.value = ''
}

watch(executionId, loadAll, { immediate: true, flush: 'sync' })

onMounted(() => {
  window.addEventListener('resize', resizeCharts)
})

onBeforeUnmount(() => {
  disposed = true
  ++reportGeneration
  ++shareGeneration
  pendingHtmlWindows.forEach(w => w.close())
  pendingHtmlWindows.clear()
  window.removeEventListener('resize', resizeCharts)
  ;[tpsChart, rtChart, errChart].forEach(c => c?.dispose())
  tpsChart = rtChart = errChart = null
  // 离开页面中断 AI 分析 SSE 连接，避免后台悬挂
  clearTimeout(aiIdleTimer)
  aiAbortCtrl.value?.abort()
})
</script>

<style lang="scss" scoped>
.perf-report { padding: 16px; }

.report-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  margin-bottom: 12px;
  background: #fff;
  border-radius: 6px;

  .head-left { display: flex; flex: 1 1 520px; flex-wrap: wrap; align-items: center; gap: 10px; min-width: 0; }
  .head-right { display: flex; flex: 1 1 100%; flex-wrap: wrap; justify-content: space-between; gap: 12px; max-width: 100%; padding-top: 12px; border-top: 1px solid var(--el-border-color-lighter); }
  .report-actions, .report-downloads { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
  .head-left :deep(.el-tag), .head-left :deep(.el-button) { flex-shrink: 0; }
  .head-right :deep(.el-button + .el-button) { margin-left: 0; }
  .exec-no { font-weight: 600; color: #303133; font-family: Menlo, Consolas, monospace; max-width: 100%; overflow-wrap: anywhere; }
  .scenario-name { flex: 1 1 16em; min-width: min(16em, 100%); max-width: 100%; overflow-wrap: anywhere; color: #909399; font-size: 13px; }
}

.report-tabs {
  background: #fff;
  border-radius: 6px;
  padding: 0 14px 14px;
  :deep(.el-tabs__content) { padding-top: 12px; }
}

.metric-row { margin-bottom: 12px; }
.interface-card { margin: 12px 0; }
.interface-path { overflow-wrap: anywhere; white-space: normal; user-select: text; }
.interface-note { margin: 0 0 12px; color: #606266; font-size: 13px; line-height: 1.6; }
.interface-note:last-child { margin: 10px 0 0; }
.k6-interface-cards { display: none; }
@media (max-width: 900px) {
  .report-head {
    flex-wrap: wrap;
    .head-left { flex: 1 1 100%; flex-wrap: wrap; }
    .head-right { flex: 1 1 100%; flex-wrap: wrap; }
    .exec-no, .scenario-name { overflow-wrap: anywhere; }
    .head-right :deep(.el-button + .el-button) { margin-left: 0; }
  }
  .k6-interface-table { display: none; }
  .k6-interface-cards { display: grid; gap: 12px; }
  .k6-interface-item { border: 1px solid #e4e7ed; border-radius: 6px; padding: 12px; min-width: 0; }
  .interface-item-title { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; overflow-wrap: anywhere; }
  .interface-counts {
    display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin: 12px 0;
    dt { font-size: 12px; color: #606266; }
    dd { margin: 4px 0 0; font-size: 18px; font-weight: 600; overflow-wrap: anywhere; }
  }
  .interface-failure { margin: 0; font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
}
.chart-card { margin-bottom: 12px; }
.chart-box { height: 300px; }
.chart-sm { height: 200px; }
.snapshot-tip { margin-bottom: 12px; }
.mb12 { margin-bottom: 12px; }
.mt12 { margin-top: 12px; }
.baseline-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  font-size: 13px;
  color: #909399;
}
.ok { color: #52c41a; }
.bad { color: #f5222d; }
.bad-val { color: #f5222d; font-weight: 600; }
.good-val { color: #52c41a; font-weight: 600; }

/* AI 分析 Tab */
.ai-panel {
  min-height: 320px;
  padding: 4px;
}

/* 未开始：引导卡片 */
.ai-hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 56px 24px;
  border: 1px dashed #dcdfe6;
  border-radius: 10px;
  background:
    radial-gradient(600px 200px at 50% -40px, rgba(64, 158, 255, 0.08), transparent),
    #fff;

  .ai-hero-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 64px;
    height: 64px;
    border-radius: 18px;
    color: #fff;
    background: linear-gradient(135deg, #409eff 0%, #7c4dff 100%);
    box-shadow: 0 8px 20px rgba(64, 158, 255, 0.35);
    margin-bottom: 18px;
  }
  .ai-hero-title {
    margin: 0 0 8px;
    font-size: 18px;
    font-weight: 600;
    color: #303133;
  }
  .ai-hero-desc {
    margin: 0 0 18px;
    max-width: 460px;
    font-size: 13px;
    line-height: 1.7;
    color: #909399;
  }
  .ai-hero-features {
    display: flex;
    gap: 10px;
    margin-bottom: 24px;
    flex-wrap: wrap;
    justify-content: center;
  }
  .ai-feature {
    padding: 4px 12px;
    font-size: 12px;
    color: #409eff;
    background: rgba(64, 158, 255, 0.08);
    border: 1px solid rgba(64, 158, 255, 0.25);
    border-radius: 999px;
  }
  .ai-start-btn {
    padding: 11px 28px;
    background: linear-gradient(135deg, #409eff 0%, #7c4dff 100%);
    border: none;
    box-shadow: 0 6px 16px rgba(64, 158, 255, 0.35);
    &:hover { opacity: 0.92; }
  }
}

/* 分析中（尚无文本） */
.ai-streaming-hint {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 80px 0;
  font-size: 14px;
  color: #409eff;

  .ai-dots i {
    font-style: normal;
    animation: ai-dot 1.2s infinite;
    &:nth-child(2) { animation-delay: 0.2s; }
    &:nth-child(3) { animation-delay: 0.4s; }
  }
}
@keyframes ai-dot {
  0%, 60%, 100% { opacity: 0.2; }
  30% { opacity: 1; }
}

/* 分析结果卡片 */
.ai-card {
  border: 1px solid #ebeef5;
  border-radius: 10px;
  overflow: hidden;
  background: #fff;

  .ai-card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 16px;
    background: linear-gradient(90deg, rgba(64, 158, 255, 0.08), rgba(124, 77, 255, 0.06));
    border-bottom: 1px solid #ebeef5;
  }
  .ai-card-title {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .ai-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    font-weight: 600;
    background: linear-gradient(135deg, #409eff, #7c4dff);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
  }
  .ai-streaming-tag {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    color: #409eff;
  }
  .ai-card-actions { display: flex; gap: 4px; }
  .ai-card-body { padding: 20px 24px; }
}

/* 流式输出光标 */
:deep(.ai-streaming-cursor) {
  display: inline-block;
  width: 8px;
  height: 15px;
  margin-left: 3px;
  vertical-align: text-bottom;
  background: #409eff;
  border-radius: 1px;
  animation: ai-blink 1s steps(1) infinite;
}
@keyframes ai-blink {
  0%, 50% { opacity: 1; }
  51%, 100% { opacity: 0; }
}

/* 失败提示 */
.ai-error {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 12px;
  .el-alert { flex: 1; }
}

/* Markdown 正文样式 */
.markdown-body {
  font-size: 14px;
  line-height: 1.8;
  color: #303133;
  word-break: break-word;

  :deep(h1), :deep(h2), :deep(h3), :deep(h4) {
    color: #1f2d3d;
    margin: 18px 0 10px;
    line-height: 1.4;
    &:first-child { margin-top: 0; }
  }
  :deep(h1) { font-size: 18px; }
  :deep(h2) {
    font-size: 16px;
    padding-left: 10px;
    border-left: 3px solid #409eff;
  }
  :deep(h3) { font-size: 15px; }
  :deep(p) { margin: 8px 0; }
  :deep(ul), :deep(ol) { margin: 8px 0; padding-left: 22px; }
  :deep(li) { margin: 4px 0; }
  :deep(li::marker) { color: #409eff; }
  :deep(strong) { color: #1f2d3d; }
  :deep(blockquote) {
    margin: 12px 0;
    padding: 8px 16px;
    border-left: 4px solid #409eff;
    border-radius: 0 6px 6px 0;
    background: #f5f7fa;
    color: #606266;
  }
  :deep(code) {
    padding: 2px 6px;
    font-size: 13px;
    font-family: Consolas, Monaco, Menlo, monospace;
    color: #c7254e;
    background: #f5f7fa;
    border-radius: 4px;
  }
  :deep(pre) {
    margin: 12px 0;
    padding: 14px 16px;
    overflow-x: auto;
    background: #282c34;
    border-radius: 6px;
    code { padding: 0; color: #abb2bf; background: transparent; }
  }
  :deep(table) {
    width: 100%;
    margin: 12px 0;
    border-collapse: collapse;
    th, td { padding: 8px 12px; border: 1px solid #ebeef5; text-align: left; }
    th { background: #f5f7fa; font-weight: 600; }
    tr:nth-child(even) { background: #fafafa; }
  }
  :deep(hr) { margin: 18px 0; border: none; border-top: 1px solid #ebeef5; }
  :deep(a) { color: #409eff; text-decoration: none; }
}
</style>
