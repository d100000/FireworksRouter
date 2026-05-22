<template>
  <div class="health-signal" :title="tooltipText">
    <div class="dots">
      <span class="dot" :class="requestClass" :title="requestTitle"></span>
      <span class="dot" :class="probeClass" :title="probeTitle"></span>
    </div>
    <Sparkline :buckets="buckets" :width="84" :height="22" />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import Sparkline from './Sparkline.vue'

const props = defineProps({
  buckets: { type: Array, default: () => [] },
  lastProbeOk: { type: Boolean, default: null },
  lastProbeMs: { type: Number, default: 0 },
  lastProbeAt: { type: String, default: '' },
})

// 「请求通畅」逻辑：最近 1 个桶（=最近 10min）只要有成功就 OK；全失败 = 红；零请求 = 灰
const requestClass = computed(() => {
  const last = props.buckets[props.buckets.length - 1]
  if (!last || (last.success === 0 && last.failed === 0)) return 'grey'
  if (last.success > 0 && last.failed === 0) return 'green pulse'
  if (last.success > 0 && last.failed > 0) return 'amber'
  return 'red'
})

const requestTitle = computed(() => {
  const last = props.buckets[props.buckets.length - 1]
  if (!last) return '请求：无数据'
  if (last.success === 0 && last.failed === 0) return '请求：最近 10 分钟无请求'
  return `请求：最近 10 分钟 ✓${last.success} ✗${last.failed}`
})

const probeClass = computed(() => {
  if (props.lastProbeOk === null || props.lastProbeOk === undefined) return 'grey'
  return props.lastProbeOk ? 'green' : 'red'
})

const probeTitle = computed(() => {
  if (props.lastProbeOk === null) return '探针：未运行过'
  const at = props.lastProbeAt ? new Date(props.lastProbeAt).toLocaleTimeString() : ''
  return `探针：${props.lastProbeOk ? '成功' : '失败'}${props.lastProbeMs ? ` (${props.lastProbeMs}ms)` : ''}${at ? ' · ' + at : ''}`
})

const tooltipText = computed(() => requestTitle.value + '\n' + probeTitle.value)
</script>

<style scoped>
.health-signal {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.dots {
  display: inline-flex;
  flex-direction: column;
  gap: 3px;
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  display: inline-block;
}
.dot.green { background: #22c55e; box-shadow: 0 0 0 2px rgba(34, 197, 94, 0.18); }
.dot.amber { background: #f59e0b; box-shadow: 0 0 0 2px rgba(245, 158, 11, 0.18); }
.dot.red   { background: #ef4444; box-shadow: 0 0 0 2px rgba(239, 68, 68, 0.18); }
.dot.grey  { background: #94a3b8; box-shadow: 0 0 0 2px rgba(148, 163, 184, 0.15); }
.dot.pulse { animation: hb-pulse 2s ease-in-out infinite; }
@keyframes hb-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}
</style>
