<template>
  <div class="balance-cell" :title="tooltipText">
    <div class="balance-row">
      <span class="balance-amount">${{ balance.toFixed(2) }}</span>
      <span class="balance-divider">/</span>
      <span class="balance-limit">${{ limit.toFixed(0) }}</span>
      <span class="balance-pct" :class="levelClass">{{ pct.toFixed(0) }}%</span>
    </div>
    <div class="balance-track">
      <div class="balance-fill" :class="levelClass" :style="{ width: pct + '%' }"></div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  balance: { type: Number, default: 0 },
  limit: { type: Number, default: 0 },
  used: { type: Number, default: 0 },
  pct: { type: Number, default: 0 }, // 后端已计算
})

const levelClass = computed(() => {
  if (props.pct >= 60) return 'lv-high'
  if (props.pct >= 20) return 'lv-mid'
  return 'lv-low'
})

const tooltipText = computed(() =>
  `剩余 $${props.balance.toFixed(4)}\n本月已用 $${props.used.toFixed(4)}\n月上限 $${props.limit.toFixed(2)}\n剩余比例 ${props.pct.toFixed(1)}%`
)
</script>

<style scoped>
.balance-cell { min-width: 130px; }

.balance-row {
  display: flex;
  align-items: baseline;
  gap: 3px;
  font-size: 12px;
  line-height: 1.2;
  margin-bottom: 4px;
}
.balance-amount { font-weight: 600; color: var(--n-text-color); }
.balance-divider { color: var(--n-text-color-3, #94a3b8); }
.balance-limit { color: var(--n-text-color-3, #94a3b8); }
.balance-pct {
  margin-left: auto;
  font-weight: 600;
  font-size: 11px;
}
.balance-pct.lv-high { color: #22c55e; }
.balance-pct.lv-mid  { color: #f59e0b; }
.balance-pct.lv-low  { color: #ef4444; }

.balance-track {
  height: 5px;
  background: var(--n-color-target, rgba(148, 163, 184, 0.18));
  border-radius: 3px;
  overflow: hidden;
}
.balance-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s;
}
.balance-fill.lv-high { background: linear-gradient(90deg, #14b8a6, #22c55e); }
.balance-fill.lv-mid  { background: linear-gradient(90deg, #f59e0b, #fbbf24); }
.balance-fill.lv-low  { background: linear-gradient(90deg, #ef4444, #f87171); }
</style>
