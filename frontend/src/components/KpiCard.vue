<template>
  <n-card :bordered="false" class="kpi-card fwr-card-hover" content-style="padding: 16px">
    <div class="kpi-row">
      <div class="fwr-kpi-icon" :style="{ background: bgColor, color: fgColor }">
        <n-icon size="22"><component :is="icon" /></n-icon>
      </div>
      <div class="kpi-text">
        <div class="kpi-label">{{ label }}</div>
        <div class="kpi-value">
          {{ value }}
          <span v-if="suffix" class="kpi-suffix">{{ suffix }}</span>
        </div>
        <div v-if="sub" class="kpi-sub" :class="trendClass">
          <span v-if="trend === 'up'">↗</span>
          <span v-else-if="trend === 'down'">↘</span>
          {{ sub }}
        </div>
      </div>
    </div>
  </n-card>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  label: String,
  value: [String, Number],
  suffix: String,
  sub: String,
  trend: { type: String, default: '' }, // up / down / ''
  icon: Object,
  color: { type: String, default: 'teal' }, // teal / blue / purple / green / amber / rose / violet / sky / indigo
})

const COLOR_MAP = {
  teal:   { bg: 'rgba(20, 184, 166, 0.12)', fg: '#14b8a6' },
  blue:   { bg: 'rgba(59, 130, 246, 0.12)', fg: '#3b82f6' },
  purple: { bg: 'rgba(168, 85, 247, 0.12)', fg: '#a855f7' },
  green:  { bg: 'rgba(34, 197, 94, 0.12)',  fg: '#22c55e' },
  amber:  { bg: 'rgba(245, 158, 11, 0.14)', fg: '#f59e0b' },
  rose:   { bg: 'rgba(244, 63, 94, 0.12)',  fg: '#f43f5e' },
  violet: { bg: 'rgba(139, 92, 246, 0.12)', fg: '#8b5cf6' },
  sky:    { bg: 'rgba(14, 165, 233, 0.12)', fg: '#0ea5e9' },
  indigo: { bg: 'rgba(99, 102, 241, 0.12)', fg: '#6366f1' },
  emerald:{ bg: 'rgba(16, 185, 129, 0.12)', fg: '#10b981' },
}

const bgColor = computed(() => (COLOR_MAP[props.color] || COLOR_MAP.teal).bg)
const fgColor = computed(() => (COLOR_MAP[props.color] || COLOR_MAP.teal).fg)
const trendClass = computed(() => props.trend === 'up' ? 'up' : props.trend === 'down' ? 'down' : '')
</script>

<style scoped>
.kpi-card {
  height: 100%;
}
.kpi-row {
  display: flex;
  align-items: center;
  gap: 14px;
}
.kpi-text { flex: 1; min-width: 0; }
.kpi-label {
  font-size: 12px;
  color: var(--n-text-color-3, #64748b);
  margin-bottom: 4px;
  font-weight: 500;
}
.kpi-value {
  font-size: 22px;
  font-weight: 700;
  color: var(--n-text-color);
  line-height: 1.2;
}
.kpi-suffix {
  font-size: 12px;
  font-weight: 500;
  color: var(--n-text-color-3, #64748b);
  margin-left: 2px;
}
.kpi-sub {
  font-size: 11px;
  color: var(--n-text-color-3, #94a3b8);
  margin-top: 4px;
}
.kpi-sub.up { color: #22c55e; }
.kpi-sub.down { color: #f43f5e; }
</style>
