<template>
  <n-space vertical size="large">
    <!-- 控制条 -->
    <n-card size="small">
      <n-space align="center" justify="space-between">
        <n-space align="center">
          <n-text strong>调度策略：</n-text>
          <n-tag :type="strategyTagType" size="small">{{ strategy }}</n-tag>
          <n-text depth="3">·</n-text>
          <n-text depth="3">共 {{ points.length }} 次请求</n-text>
          <n-text depth="3" v-if="lastUpdate">· 更新于 {{ lastUpdate }}</n-text>
        </n-space>
        <n-space align="center">
          <n-text depth="3">时间窗</n-text>
          <n-select
            v-model:value="minutes"
            :options="windowOpts"
            style="width: 110px"
            size="small"
            @update:value="refresh"
          />
          <n-text depth="3">自动刷新（5s）</n-text>
          <n-switch v-model:value="autoRefresh" />
          <n-button size="small" @click="refresh" :loading="loading">手动刷新</n-button>
        </n-space>
      </n-space>
    </n-card>

    <!-- 散点图：请求轨迹 -->
    <n-card title="请求轨迹（散点：横轴时间、纵轴上游 Key、颜色=状态码、大小=延迟）">
      <v-chart
        v-if="points.length"
        :option="scatterOption"
        :theme="themeStore.isDark ? 'dark' : ''"
        autoresize
        style="height: 380px"
      />
      <n-empty v-else description="选定时间窗内无请求" />

      <n-divider />

      <n-space align="center">
        <n-text depth="3" style="font-size: 12px">图例：</n-text>
        <n-tag size="tiny" :color="{color:'#52c41a',textColor:'#fff'}">2xx 成功</n-tag>
        <n-tag size="tiny" :color="{color:'#faad14',textColor:'#fff'}">4xx 客户端</n-tag>
        <n-tag size="tiny" :color="{color:'#f5222d',textColor:'#fff'}">5xx 服务端</n-tag>
        <n-tag size="tiny" :color="{color:'#8c8c8c',textColor:'#fff'}">其它</n-tag>
        <n-text depth="3" style="font-size: 12px; margin-left: 12px">点大小 = 延迟（ms）</n-text>
      </n-space>
    </n-card>

    <!-- 桑基图：流量分发 -->
    <n-card title="流量分发（API Key → 模型 → 上游 Key）">
      <v-chart
        v-if="sankey && sankey.links.length"
        :option="sankeyOption"
        :theme="themeStore.isDark ? 'dark' : ''"
        autoresize
        style="height: 420px"
      />
      <n-empty v-else description="过去 24h 内无聚合数据" />
    </n-card>

    <!-- 最近请求列表 -->
    <n-card title="最近请求明细（点散点也能联动）" size="small">
      <n-data-table size="small" :columns="logCols" :data="points.slice(0, 50)" :pagination="{ pageSize: 10 }" :scroll-x="1400" />
    </n-card>
  </n-space>
</template>

<script setup>
import { ref, h, onMounted, onBeforeUnmount, computed, watch } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { ScatterChart, SankeyChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, VisualMapComponent, DataZoomComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { NTag } from 'naive-ui'
import dayjs from 'dayjs'
import { statsApi } from '@/api'
import { useThemeStore } from '@/stores/theme'

use([CanvasRenderer, ScatterChart, SankeyChart, GridComponent, TooltipComponent, LegendComponent, VisualMapComponent, DataZoomComponent])

const themeStore = useThemeStore()

const minutes = ref(60)
const windowOpts = [
  { label: '15 分钟', value: 15 },
  { label: '1 小时', value: 60 },
  { label: '6 小时', value: 360 },
  { label: '24 小时', value: 1440 },
]

const points = ref([])
const keys = ref([])
const strategy = ref('-')
const sankey = ref(null)
const loading = ref(false)
const lastUpdate = ref('')
const autoRefresh = ref(true)
let timer = null

const strategyTagType = computed(() => {
  return { fill_first: 'warning', round_robin: 'info', weighted_random: 'success', priority: 'info', session_sticky: 'success' }[strategy.value] || 'default'
})

async function refresh() {
  loading.value = true
  try {
    const [t, s] = await Promise.all([
      statsApi.requestTrace({ minutes: minutes.value, limit: 1000 }),
      statsApi.flowSankey({ hours: 24 }),
    ])
    points.value = t.data.points
    keys.value = t.data.keys
    strategy.value = t.data.strategy
    sankey.value = s.data
    lastUpdate.value = dayjs().format('HH:mm:ss')
  } finally { loading.value = false }
}

function startTimer() { stopTimer(); if (autoRefresh.value) timer = setInterval(refresh, 5000) }
function stopTimer() { if (timer) { clearInterval(timer); timer = null } }
watch(autoRefresh, startTimer)

// ============ Scatter ============
function statusColor(code) {
  if (code >= 200 && code < 300) return '#52c41a'
  if (code >= 400 && code < 500) return '#faad14'
  if (code >= 500) return '#f5222d'
  return '#8c8c8c'
}

const scatterOption = computed(() => {
  const keyLabels = keys.value.map(k => `#${k.id} ${k.name}`).reverse()
  const keyIdToY = {}
  keys.value.forEach((k, i) => { keyIdToY[k.id] = keys.value.length - 1 - i })

  const data = points.value.map(p => ({
    value: [
      new Date(p.ts).getTime(),
      keyIdToY[p.upstream_key_id] ?? 0,
      p.latency_ms,
    ],
    itemStyle: { color: statusColor(p.status_code) },
    // 自定义数据用于 tooltip
    _meta: p,
  }))

  return {
    tooltip: {
      trigger: 'item',
      formatter: (params) => {
        const p = params.data._meta
        return `
          <b>${dayjs(p.ts).format('HH:mm:ss.SSS')}</b><br/>
          上游 Key: ${p.upstream_key_preview} (#${p.upstream_key_id})<br/>
          API Key: ${p.api_key_label || '-'}<br/>
          模型: ${p.public_model}<br/>
          状态: <b>${p.status_code}</b> · 延迟: <b>${p.latency_ms}ms</b>${p.ttft_ms ? ' · TTFT ' + p.ttft_ms + 'ms' : ''}<br/>
          tokens: ${p.prompt_tokens}+${p.completion_tokens}${p.retry_count ? ' · 重试 ' + p.retry_count : ''}<br/>
          ${p.stream ? '🌊 流式' : '一次性'}<br/>
          request_id: ${p.request_id}
        `
      },
    },
    grid: { left: 140, right: 30, top: 30, bottom: 60 },
    xAxis: {
      type: 'time',
      name: '时间',
      splitLine: { show: true, lineStyle: { type: 'dashed', opacity: 0.3 } },
    },
    yAxis: {
      type: 'category',
      data: keyLabels,
      name: '上游 Key',
      splitLine: { show: true, lineStyle: { type: 'dashed', opacity: 0.3 } },
    },
    dataZoom: [
      { type: 'inside', xAxisIndex: 0 },
      { type: 'slider', xAxisIndex: 0, height: 20, bottom: 10 },
    ],
    series: [{
      type: 'scatter',
      data: data,
      symbolSize: (val) => {
        const ms = val[2] || 0
        return Math.max(6, Math.min(40, Math.sqrt(ms) * 0.8))
      },
      emphasis: { focus: 'series' },
    }],
  }
})

// ============ Sankey ============
const sankeyOption = computed(() => {
  if (!sankey.value) return {}
  // 给不同 layer 上色
  const layerColor = { api_key: '#5470c6', model: '#91cc75', upstream: '#fac858' }
  const sankeyNodes = sankey.value.nodes.map(n => ({
    name: n.name,
    itemStyle: { color: layerColor[n.layer] || '#8c8c8c' },
  }))
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c}' },
    series: [{
      type: 'sankey',
      data: sankeyNodes,
      links: sankey.value.links,
      lineStyle: { curveness: 0.5, opacity: 0.5 },
      label: { fontSize: 12 },
      nodeWidth: 22,
      nodeGap: 8,
      emphasis: { focus: 'adjacency' },
    }],
  }
})

// ============ 列表 ============
const codeType = (c) => c >= 500 ? 'error' : c >= 400 ? 'warning' : c >= 200 ? 'success' : 'default'

const logCols = [
  { title: '时间', key: 'ts', width: 110, render: (p) => dayjs(p.ts).format('HH:mm:ss.SSS') },
  { title: '上游 Key', key: 'upstream_key_preview', width: 150 },
  { title: 'API Key', key: 'api_key_label', width: 160, ellipsis: { tooltip: true } },
  { title: '模型', key: 'public_model', width: 200, ellipsis: { tooltip: true } },
  {
    title: '状态', key: 'status_code', width: 80,
    render: (p) => h(NTag, { size: 'small', type: codeType(p.status_code) }, () => p.status_code),
  },
  { title: '流式', key: 'stream', width: 60, render: (p) => p.stream ? '🌊' : '' },
  { title: 'Tokens', key: 't', width: 100, render: (p) => `${p.prompt_tokens}+${p.completion_tokens}` },
  { title: '延迟', key: 'latency_ms', width: 80, render: (p) => `${p.latency_ms}ms` },
  { title: 'TTFT', key: 'ttft_ms', width: 80, render: (p) => p.ttft_ms ? `${p.ttft_ms}ms` : '-' },
  { title: '重试', key: 'retry_count', width: 60 },
]

onMounted(() => { refresh(); startTimer() })
onBeforeUnmount(stopTimer)
</script>
