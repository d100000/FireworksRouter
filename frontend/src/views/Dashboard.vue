<template>
  <n-space vertical :size="20">
    <!-- 顶部 -->
    <div class="dashboard-header">
      <div>
        <h2 class="page-title">总览</h2>
        <p class="page-sub">{{ lastUpdate ? `最近更新于 ${lastUpdate}` : '正在加载…' }}</p>
      </div>
      <n-space align="center">
        <n-text depth="3" style="font-size: 13px">自动刷新 5s</n-text>
        <n-switch v-model:value="autoRefresh" size="small" />
        <n-button size="small" @click="refreshAll" :loading="loading">手动刷新</n-button>
      </n-space>
    </div>

    <!-- KPI -->
    <n-grid :cols="4" :x-gap="16" :y-gap="16" responsive="screen" :item-responsive="true">
      <n-gi v-for="card in cards" :key="card.label">
        <KpiCard
          :label="card.label"
          :value="card.value"
          :suffix="card.suffix"
          :sub="card.sub"
          :icon="card.icon"
          :color="card.color"
        />
      </n-gi>
    </n-grid>

    <!-- Key 健康 -->
    <n-grid :cols="2" :x-gap="16" :y-gap="16" responsive="screen" :item-responsive="true" v-if="keysHealth">
      <n-gi>
        <n-card title="🟢 稳定性 Top 5" :bordered="false">
          <n-data-table size="small" :columns="healthCols" :data="keysHealth.top_stable" :pagination="false" :bordered="false" :single-line="false" />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card title="🔴 稳定性垫底 / 冷却中" :bordered="false">
          <n-data-table size="small" :columns="healthCols" :data="keysHealth.bottom_stable" :pagination="false" :bordered="false" :single-line="false" v-if="keysHealth.bottom_stable?.length" />
          <div v-else class="empty-mini">所有 Key 状态良好 ✨</div>
        </n-card>
      </n-gi>
    </n-grid>

    <!-- 趋势 -->
    <n-card title="近 24 小时趋势" :bordered="false">
      <v-chart
        v-if="series.length"
        :option="chartOption"
        :theme="themeStore.isDark ? 'dark' : ''"
        autoresize
        style="height: 320px"
      />
      <div v-else class="empty-mini" style="padding: 60px 0">暂无数据 — 开始调用 /v1/chat/completions 后约 30s 入库</div>
    </n-card>

    <!-- Top -->
    <n-grid :cols="3" :x-gap="16" :y-gap="16" responsive="screen" :item-responsive="true">
      <n-gi>
        <n-card title="Top API Key" :bordered="false">
          <n-data-table size="small" :columns="topCols" :data="topApiKeys" :pagination="false" :bordered="false" />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card title="Top 模型" :bordered="false">
          <n-data-table size="small" :columns="topCols" :data="topModels" :pagination="false" :bordered="false" />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card title="Top 上游 Key" :bordered="false">
          <n-data-table size="small" :columns="topCols" :data="topUpstream" :pagination="false" :bordered="false" />
        </n-card>
      </n-gi>
    </n-grid>
  </n-space>
</template>

<script setup>
import { ref, h, onMounted, onBeforeUnmount, computed, watch } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import dayjs from 'dayjs'
import { statsApi } from '@/api'
import { useThemeStore } from '@/stores/theme'
import KpiCard from '@/components/KpiCard.vue'
import StatusDot from '@/components/StatusDot.vue'
import {
  PulseOutline, FlashOutline, WalletOutline, BanOutline,
  TrendingUpOutline, LayersOutline, KeyOutline, ServerOutline,
} from '@vicons/ionicons5'

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent])

const themeStore = useThemeStore()

const overview = ref(null)
const today = ref(null)
const series = ref([])
const topApiKeys = ref([])
const topModels = ref([])
const topUpstream = ref([])
const keysHealth = ref(null)

const loading = ref(false)
const autoRefresh = ref(true)
const lastUpdate = ref('')

let timer = null

async function refreshAll() {
  loading.value = true
  try {
    const [o, t, ts, ta, tm, tu, kh] = await Promise.all([
      statsApi.overview(), statsApi.today(),
      statsApi.timeseries({ period_hours: 24, bucket: 'hour' }),
      statsApi.top({ dimension: 'api_key', period_hours: 24, limit: 5 }),
      statsApi.top({ dimension: 'model', period_hours: 24, limit: 5 }),
      statsApi.top({ dimension: 'upstream', period_hours: 24, limit: 5 }),
      statsApi.keysHealth(),
    ])
    overview.value = o.data; today.value = t.data; series.value = ts.data.series
    topApiKeys.value = ta.data.items; topModels.value = tm.data.items; topUpstream.value = tu.data.items
    keysHealth.value = kh.data
    lastUpdate.value = dayjs().format('HH:mm:ss')
  } finally { loading.value = false }
}

function startTimer() { stopTimer(); if (autoRefresh.value) timer = setInterval(refreshAll, 5000) }
function stopTimer() { if (timer) { clearInterval(timer); timer = null } }
watch(autoRefresh, startTimer)

const cards = computed(() => {
  if (!overview.value || !today.value) return []
  return [
    { label: '上游 Active', value: overview.value.upstream.active, icon: PulseOutline, color: 'teal' },
    { label: '冷却中', value: overview.value.upstream.in_cooldown, icon: BanOutline, color: 'amber' },
    { label: '上游总余额', value: overview.value.upstream.total_balance_usd.toFixed(2), suffix: 'USD', icon: WalletOutline, color: 'emerald' },
    { label: '自动禁用', value: overview.value.upstream.auto_disabled, icon: BanOutline, color: 'rose' },
    { label: '今日请求', value: today.value.requests.toLocaleString(), icon: FlashOutline, color: 'blue' },
    { label: '今日 Tokens', value: today.value.total_tokens.toLocaleString(), icon: LayersOutline, color: 'violet' },
    { label: '今日成本', value: today.value.billed_cost_usd.toFixed(4), suffix: 'USD', icon: TrendingUpOutline, color: 'purple' },
    { label: 'API Keys', value: overview.value.api_keys_total, icon: KeyOutline, color: 'sky' },
  ]
})

const chartOption = computed(() => {
  const xs = series.value.map(s => dayjs(s.ts).format('HH:mm'))
  return {
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(15, 23, 42, 0.95)', borderColor: '#0f766e', textStyle: { color: '#fff' } },
    legend: { data: ['请求数', 'Tokens', '成本'], top: 0, textStyle: { color: themeStore.isDark ? '#cbd5e1' : '#475569' } },
    grid: { left: 50, right: 50, top: 40, bottom: 30 },
    xAxis: {
      type: 'category', data: xs, boundaryGap: false,
      axisLine: { lineStyle: { color: themeStore.isDark ? '#334155' : '#e2e8f0' } },
      axisLabel: { color: themeStore.isDark ? '#94a3b8' : '#64748b' },
    },
    yAxis: [
      { type: 'value', name: '次/数', splitLine: { lineStyle: { type: 'dashed', color: themeStore.isDark ? '#334155' : '#f1f5f9' } }, axisLabel: { color: themeStore.isDark ? '#94a3b8' : '#64748b' } },
      { type: 'value', name: 'USD', position: 'right', splitLine: { show: false }, axisLabel: { color: themeStore.isDark ? '#94a3b8' : '#64748b' } },
    ],
    color: ['#14b8a6', '#8b5cf6', '#f59e0b'],
    series: [
      { name: '请求数', type: 'line', smooth: true, data: series.value.map(s => s.requests), areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(20, 184, 166, 0.3)' }, { offset: 1, color: 'rgba(20, 184, 166, 0)' }] } }, symbol: 'circle', symbolSize: 6, lineStyle: { width: 2.5 } },
      { name: 'Tokens', type: 'line', smooth: true, data: series.value.map(s => s.tokens), symbol: 'circle', symbolSize: 6, lineStyle: { width: 2 } },
      { name: '成本', type: 'line', smooth: true, yAxisIndex: 1, data: series.value.map(s => Number(s.cost_usd.toFixed(6))), symbol: 'circle', symbolSize: 6, lineStyle: { width: 2 } },
    ],
  }
})

const scoreType = (s) => s >= 0.95 ? 'success' : s >= 0.8 ? 'info' : s >= 0.5 ? 'warning' : 'danger'

const healthCols = [
  { title: 'Key', key: 'name', ellipsis: { tooltip: true } },
  {
    title: '评分', key: 'stability_score', width: 100,
    render: (r) => h('span', {
      style: {
        fontWeight: 600,
        color: r.stability_score >= 0.95 ? '#22c55e' : r.stability_score >= 0.8 ? '#3b82f6' : r.stability_score >= 0.5 ? '#f59e0b' : '#ef4444',
      },
    }, `${(r.stability_score * 100).toFixed(0)}%`),
  },
  { title: '24h ✓', key: 'success_count_24h', width: 60 },
  { title: '24h ✗', key: 'failed_count_24h', width: 60 },
]

const topCols = [
  { title: 'Key', key: 'key', ellipsis: { tooltip: true } },
  { title: '次', key: 'requests', width: 60 },
  { title: '成本', key: 'cost_usd', width: 100, render: (r) => h('span', { style: 'color: #0d9488; font-weight: 500' }, `$${r.cost_usd.toFixed(5)}`) },
]

onMounted(() => { refreshAll(); startTimer() })
onBeforeUnmount(stopTimer)
</script>

<style scoped>
.dashboard-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.page-title {
  font-size: 22px;
  font-weight: 700;
  margin: 0;
  letter-spacing: -0.3px;
}
.page-sub {
  font-size: 13px;
  color: var(--n-text-color-3, #64748b);
  margin: 4px 0 0 0;
}
.empty-mini {
  text-align: center;
  padding: 20px;
  color: var(--n-text-color-3, #94a3b8);
  font-size: 13px;
}
</style>
