<template>
  <svg :width="width" :height="height" class="sparkline" :viewBox="`0 0 ${width} ${height}`" preserveAspectRatio="none">
    <!-- 背景网格 -->
    <line :x1="0" :y1="height - 1" :x2="width" :y2="height - 1" stroke="currentColor" stroke-opacity="0.1" stroke-width="1" />

    <!-- 0 数据时的提示 -->
    <text v-if="totalSum === 0" :x="width / 2" :y="height / 2 + 3" text-anchor="middle"
          fill="currentColor" fill-opacity="0.35" font-size="10">无数据</text>

    <g v-else>
      <g v-for="(b, i) in buckets" :key="i">
        <!-- success 部分（绿，从底向上） -->
        <rect
          v-if="b.success > 0"
          :x="i * barWidth + 1"
          :y="height - heightOf(b.success + b.failed)"
          :width="barWidth - 2"
          :height="heightOf(b.success)"
          fill="#22c55e"
          :opacity="0.85"
          rx="1.5"
        />
        <!-- failed 部分（红，叠在 success 顶部） -->
        <rect
          v-if="b.failed > 0"
          :x="i * barWidth + 1"
          :y="height - heightOf(b.success + b.failed) - 0.5"
          :width="barWidth - 2"
          :height="heightOf(b.failed)"
          fill="#ef4444"
          :opacity="0.9"
          rx="1.5"
        />
      </g>
    </g>

    <title>{{ tooltip }}</title>
  </svg>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  buckets: { type: Array, default: () => [] }, // [{ts, success, failed, avg_ms}]
  width: { type: Number, default: 92 },
  height: { type: Number, default: 28 },
})

const barWidth = computed(() => props.width / Math.max(1, props.buckets.length))

const totalSum = computed(() =>
  props.buckets.reduce((s, b) => s + (b.success || 0) + (b.failed || 0), 0)
)

const maxBucket = computed(() => {
  let m = 0
  for (const b of props.buckets) {
    const t = (b.success || 0) + (b.failed || 0)
    if (t > m) m = t
  }
  return Math.max(1, m)
})

function heightOf(count) {
  if (count <= 0) return 0
  // 至少 2px 高（让 1 次请求也看得到）
  return Math.max(2, Math.round((count / maxBucket.value) * (props.height - 3)))
}

const tooltip = computed(() => {
  if (!props.buckets.length) return ''
  return props.buckets
    .map((b) => {
      const t = b.ts ? new Date(b.ts).toLocaleTimeString().slice(0, 5) : ''
      return `${t}: ✓${b.success || 0} ✗${b.failed || 0}${b.avg_ms ? ` (${b.avg_ms}ms)` : ''}`
    })
    .join('\n')
})
</script>

<style scoped>
.sparkline {
  display: block;
  color: var(--n-text-color-3, #94a3b8);
  vertical-align: middle;
}
</style>
