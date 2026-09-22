<template>
  <div class="metric-cards" :style="{ '--cols': cols }">
    <div
      v-for="item in items"
      :key="item.key"
      class="metric-card"
      :class="item.status || 'normal'"
    >
      <div class="mc-label">{{ item.label }}</div>
      <div class="mc-value">
        {{ item.value }}
        <span v-if="item.unit" class="mc-unit">{{ item.unit }}</span>
      </div>
      <div v-if="item.threshold !== undefined && item.threshold !== null" class="mc-threshold">
        <span class="mc-th-label">{{ $t('performanceTesting.monitor.threshold') }}</span>
        <span class="mc-th-value">{{ item.threshold }}{{ item.unit || '' }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  items: { type: Array, default: () => [] },
  // 一行显示几列（响应式：移动端自动折行）
  cols: { type: Number, default: 5 }
})

const { t } = useI18n()
// 暴露给模板（避免未使用告警）
void t
const cols = computed(() => props.cols)
</script>

<style lang="scss" scoped>
.metric-cards {
  display: grid;
  grid-template-columns: repeat(var(--cols, 5), 1fr);
  gap: 12px;
}
.metric-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 14px 16px;
  position: relative;
  overflow: hidden;
  &::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    background: #1890ff;
  }
  &.good::before { background: #52c41a; }
  &.warn::before { background: #faad14; }
  &.bad::before { background: #ff4d4f; }
  &.good .mc-value { color: #52c41a; }
  &.warn .mc-value { color: #faad14; }
  &.bad .mc-value { color: #ff4d4f; }
}
.mc-label {
  font-size: 13px;
  color: #8c8c8c;
  margin-bottom: 6px;
}
.mc-value {
  font-size: 24px;
  font-weight: 700;
  color: #1890ff;
  line-height: 1.1;
}
.mc-unit {
  font-size: 13px;
  font-weight: 500;
  margin-left: 2px;
  color: #8c8c8c;
}
.mc-threshold {
  margin-top: 6px;
  font-size: 12px;
  color: #bfbfbf;
  display: flex;
  gap: 4px;
  align-items: baseline;
}
.mc-th-value { color: #8c8c8c; font-weight: 600; }

@media screen and (max-width: 1280px) {
  .metric-cards { grid-template-columns: repeat(3, 1fr); }
}
@media screen and (max-width: 768px) {
  .metric-cards { grid-template-columns: repeat(2, 1fr); }
}
</style>
