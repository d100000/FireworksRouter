<template>
  <n-space vertical>
    <n-space justify="space-between">
      <n-space>
        <n-button type="primary" @click="openAdd">添加 Key</n-button>
        <n-button @click="openBatch">批量导入</n-button>
        <n-button @click="probeAll" :loading="probing">全量探针</n-button>
        <n-button @click="load">刷新</n-button>
      </n-space>
      <n-text depth="3">共 {{ rows.length }} 个 · 冷却中 {{ inCooldownCount }} 个</n-text>
    </n-space>

    <n-data-table :columns="columns" :data="rows" :loading="loading" size="small" :scroll-x="1900" />

    <!-- 添加 -->
    <n-modal v-model:show="addShow" preset="card" title="添加上游 Key" style="width: 540px">
      <n-form :model="addForm" label-placement="top">
        <n-form-item label="Fireworks Key (fw_xxx)" required>
          <n-input v-model:value="addForm.key" placeholder="fw_xxxxx" />
        </n-form-item>
        <n-form-item label="备注名">
          <n-input v-model:value="addForm.name" placeholder="不填则用 key 预览" />
        </n-form-item>
        <n-grid :cols="2" x-gap="12">
          <n-gi>
            <n-form-item label="优先级">
              <n-input-number v-model:value="addForm.priority" :min="0" />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item label="权重">
              <n-input-number v-model:value="addForm.weight" :min="1" />
            </n-form-item>
          </n-gi>
        </n-grid>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="addShow = false">取消</n-button>
          <n-button type="primary" :loading="addLoading" @click="submitAdd">添加</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 批量 -->
    <n-modal v-model:show="batchShow" preset="card" title="批量导入" style="width: 640px">
      <n-text depth="3">每行一个 Key，可附 <code>,name</code> 后缀。</n-text>
      <n-input v-model:value="batchText" type="textarea" :rows="10" placeholder="fw_aaa,name-1
fw_bbb,name-2
fw_ccc" />
      <template #footer>
        <n-space justify="end">
          <n-button @click="batchShow = false">取消</n-button>
          <n-button type="primary" :loading="batchLoading" @click="submitBatch">导入</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 详情抽屉 -->
    <n-drawer v-model:show="detailShow" :width="780" placement="right">
      <n-drawer-content :title="detailTitle" closable>
        <n-spin :show="detailLoading">
          <n-space vertical size="large">
            <!-- 基本信息 -->
            <n-card v-if="detailKey" title="基本状态" size="small" :bordered="false">
              <n-descriptions :column="2" bordered size="small">
                <n-descriptions-item label="状态">
                  <StatusDot :status="detailKey.status" :label="detailKey.status" />
                </n-descriptions-item>
                <n-descriptions-item label="稳定性评分">
                  <span :style="{ fontWeight: 600, color: scoreColor(detailKey.stability_score) }">
                    {{ (detailKey.stability_score * 100).toFixed(1) }}%
                  </span>
                </n-descriptions-item>
                <n-descriptions-item label="24h 成功">{{ detailKey.success_count_24h }}</n-descriptions-item>
                <n-descriptions-item label="24h 失败">{{ detailKey.failed_count_24h }}</n-descriptions-item>
                <n-descriptions-item label="余额">${{ detailKey.balance_usd?.toFixed(2) }}</n-descriptions-item>
                <n-descriptions-item label="退避层级">{{ detailKey.backoff_level }}</n-descriptions-item>
                <n-descriptions-item label="冷却到">
                  {{ detailKey.cooldown_until ? new Date(detailKey.cooldown_until).toLocaleString() : '-' }}
                </n-descriptions-item>
                <n-descriptions-item label="冷却原因">{{ detailKey.cooldown_reason || '-' }}</n-descriptions-item>
                <n-descriptions-item label="最近成功" :span="2">
                  {{ detailKey.last_success_at ? new Date(detailKey.last_success_at).toLocaleString() : '-' }}
                </n-descriptions-item>
                <n-descriptions-item label="最近错误" :span="2">{{ detailKey.last_error_message || '-' }}</n-descriptions-item>
              </n-descriptions>
            </n-card>

            <!-- 24h 趋势 -->
            <n-card title="24h 趋势" size="small">
              <v-chart
                v-if="metricsSeries.length"
                :option="chartOption"
                :theme="themeStore.isDark ? 'dark' : ''"
                autoresize
                style="height: 240px"
              />
              <n-empty v-else description="暂无数据（开始调用后约 30s 后入库）" />
            </n-card>

            <!-- 错误码分布 -->
            <n-card title="错误码分布（24h）" size="small">
              <v-chart
                v-if="errorBreakdown.length"
                :option="errorPieOption"
                :theme="themeStore.isDark ? 'dark' : ''"
                autoresize
                style="height: 220px"
              />
              <n-empty v-else description="无错误记录" />
            </n-card>

            <!-- per-model 状态 -->
            <n-card title="(Key, Model) 冷却态" size="small">
              <n-data-table v-if="modelStates.length" size="small" :columns="modelStateCols" :data="modelStates" :pagination="false" />
              <n-empty v-else description="所有 model 状态正常（ready）" />
            </n-card>
          </n-space>
        </n-spin>
      </n-drawer-content>
    </n-drawer>
  </n-space>
</template>

<script setup>
import { ref, h, onMounted, reactive, computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, PieChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { NTag, NButton, NPopconfirm, NSpace, useMessage } from 'naive-ui'
import { upstreamApi } from '@/api'
import { useThemeStore } from '@/stores/theme'
import StatusDot from '@/components/StatusDot.vue'
import BalanceBar from '@/components/BalanceBar.vue'
import HealthSignal from '@/components/HealthSignal.vue'

use([CanvasRenderer, LineChart, PieChart, GridComponent, TooltipComponent, LegendComponent])

const message = useMessage()
const themeStore = useThemeStore()
const rows = ref([])
const loading = ref(false)
const probing = ref(false)

const addShow = ref(false)
const addLoading = ref(false)
const addForm = reactive({ key: '', name: '', priority: 0, weight: 100 })

const batchShow = ref(false)
const batchLoading = ref(false)
const batchText = ref('')

const detailShow = ref(false)
const detailLoading = ref(false)
const detailKey = ref(null)
const metricsSeries = ref([])
const errorBreakdown = ref([])
const modelStates = ref([])

const inCooldownCount = computed(() => rows.value.filter(r => r.cooldown_until && new Date(r.cooldown_until) > new Date()).length)

const scoreType = (s) => s >= 0.95 ? 'success' : s >= 0.8 ? 'info' : s >= 0.5 ? 'warning' : 'error'
const scoreColor = (s) => s >= 0.95 ? '#22c55e' : s >= 0.8 ? '#3b82f6' : s >= 0.5 ? '#f59e0b' : '#ef4444'

const columns = [
  { title: 'ID', key: 'id', width: 60 },
  { title: '名称', key: 'name', width: 120 },
  { title: 'Key', key: 'key_preview', width: 140, render: (r) => h('code', { style: 'font-size:12px;color:var(--n-text-color-3,#64748b)' }, r.key_preview) },
  {
    title: '状态', key: 'status', width: 130,
    render: (r) => h(StatusDot, { status: r.status, label: r.status, pulse: r.status === 'active' }),
  },
  {
    title: '稳定性', key: 'stability_score', width: 84,
    render: (r) => h('span', {
      style: { fontWeight: 600, fontSize: '13px', color: scoreColor(r.stability_score) },
    }, `${(r.stability_score * 100).toFixed(0)}%`),
  },
  {
    title: '最近 1h（请求/探针 + 10min × 6）', key: 'sparkline', width: 200,
    render: (r) => h(HealthSignal, {
      buckets: r.recent_buckets || [],
      lastProbeOk: r.last_probe_ok,
      lastProbeMs: r.last_probe_ms,
      lastProbeAt: r.last_probe_at,
    }),
  },
  {
    title: '余额 / 上限', key: 'balance_usd', width: 170,
    render: (r) => h(BalanceBar, {
      balance: r.balance_usd ?? 0,
      limit: r.monthly_spend_limit_usd ?? 0,
      used: r.monthly_spend_used_usd ?? 0,
      pct: r.balance_percent ?? 0,
    }),
  },
  {
    title: '冷却', key: 'cooldown_until', width: 130,
    render: (r) => r.cooldown_until && new Date(r.cooldown_until) > new Date()
      ? h(NTag, { size: 'tiny', type: 'warning' }, () => `直到 ${new Date(r.cooldown_until).toLocaleTimeString()}`)
      : '-',
  },
  { title: '优先级/权重', key: 'pw', width: 100, render: (r) => `${r.priority} / ${r.weight}` },
  {
    title: '最近成功', key: 'last_success_at', width: 150,
    render: (r) => r.last_success_at ? new Date(r.last_success_at).toLocaleString() : '-',
  },
  {
    title: '操作', key: 'actions', fixed: 'right', width: 290,
    render: (row) => h(NSpace, { size: 'small' }, () => [
      h(NButton, { size: 'tiny', type: 'primary', onClick: () => openDetail(row) }, () => '详情'),
      h(NButton, { size: 'tiny', onClick: () => probeOne(row.id) }, () => '探针'),
      h(NButton, {
        size: 'tiny', type: row.enabled ? 'warning' : 'primary',
        onClick: () => toggleEnabled(row),
      }, () => row.enabled ? '禁用' : '启用'),
      h(NPopconfirm, { onPositiveClick: () => removeRow(row.id) }, {
        default: () => '确认删除？',
        trigger: () => h(NButton, { size: 'tiny', type: 'error' }, () => '删除'),
      }),
    ]),
  },
]

const detailTitle = computed(() => detailKey.value ? `${detailKey.value.name} (#${detailKey.value.id})` : '详情')

const chartOption = computed(() => {
  const xs = metricsSeries.value.map(b => new Date(b.ts).toLocaleTimeString().slice(0, 5))
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['成功', '失败', '平均延迟'] },
    grid: { left: 50, right: 50, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: xs, boundaryGap: false },
    yAxis: [
      { type: 'value', name: '次' },
      { type: 'value', name: 'ms', position: 'right' },
    ],
    series: [
      { name: '成功', type: 'line', smooth: true, data: metricsSeries.value.map(b => b.success), areaStyle: { opacity: 0.1 } },
      { name: '失败', type: 'line', smooth: true, data: metricsSeries.value.map(b => b.failed) },
      { name: '平均延迟', type: 'line', smooth: true, yAxisIndex: 1, data: metricsSeries.value.map(b => Math.round(b.avg_latency_ms)) },
    ],
  }
})

const errorPieOption = computed(() => ({
  tooltip: { trigger: 'item' },
  legend: { top: 'bottom' },
  series: [
    {
      type: 'pie', radius: '60%',
      data: errorBreakdown.value.map(e => ({ value: e.count, name: `HTTP ${e.status_code}` })),
      label: { formatter: '{b}: {c}' },
    },
  ],
}))

const modelStateCols = [
  { title: 'model_id', key: 'model_id', width: 80 },
  { title: '状态', key: 'status', width: 100 },
  { title: '冷却到', key: 'cooldown_until', render: (r) => r.cooldown_until ? new Date(r.cooldown_until).toLocaleString() : '-' },
  { title: '错误码', key: 'last_error_code', width: 80 },
  { title: '退避', key: 'backoff_level', width: 70 },
  { title: '错误信息', key: 'last_error_message', ellipsis: { tooltip: true } },
]

async function load() {
  loading.value = true
  try {
    const { data } = await upstreamApi.list()
    rows.value = data
  } catch (e) {
    message.error('加载失败: ' + (e.message || ''))
  } finally { loading.value = false }
}

async function openDetail(row) {
  detailKey.value = row
  detailShow.value = true
  detailLoading.value = true
  try {
    const [m, eb, ms] = await Promise.all([
      upstreamApi.metrics(row.id, { hours: 24 }),
      upstreamApi.errorBreakdown(row.id, { hours: 24 }),
      upstreamApi.modelStates(row.id),
    ])
    metricsSeries.value = m.data.series
    errorBreakdown.value = eb.data.items
    modelStates.value = ms.data.items
  } finally { detailLoading.value = false }
}

async function probeAll() {
  probing.value = true
  try {
    const { data } = await upstreamApi.probeAll()
    message.success(`探针完成：成功 ${data.ok} / 失败 ${data.fail} / 共 ${data.total}`)
    await load()
  } finally { probing.value = false }
}

async function probeOne(id) {
  try {
    const { data } = await upstreamApi.probe(id)
    message.success(`balance=$${data.balance_usd?.toFixed(2)} status=${data.new_status}`)
    await load()
  } catch (e) {
    message.error('探针失败')
  }
}

async function toggleEnabled(row) {
  await upstreamApi.update(row.id, { enabled: !row.enabled })
  await load()
}

async function removeRow(id) {
  await upstreamApi.delete(id)
  await load()
}

function openAdd() {
  Object.assign(addForm, { key: '', name: '', priority: 0, weight: 100 })
  addShow.value = true
}

async function submitAdd() {
  if (!addForm.key) return message.warning('请输入 Key')
  addLoading.value = true
  try {
    await upstreamApi.create({ ...addForm, name: addForm.name || undefined })
    message.success('已添加')
    addShow.value = false
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail?.error?.message || '添加失败')
  } finally { addLoading.value = false }
}

function openBatch() { batchText.value = ''; batchShow.value = true }

async function submitBatch() {
  if (!batchText.value.trim()) return
  batchLoading.value = true
  try {
    const { data } = await upstreamApi.batchCreate({ keys: batchText.value })
    message.success(`成功 ${data.created} / 重复 ${data.duplicated} / 失败 ${data.failed}`)
    batchShow.value = false
    await load()
  } finally { batchLoading.value = false }
}

onMounted(load)
</script>
