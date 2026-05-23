<template>
  <n-space vertical size="large">
    <!-- 顶部筛选条 -->
    <n-card size="small" :bordered="false">
      <div class="filter-row">
        <n-select
          v-model:value="filter.period"
          :options="periodOpts"
          placeholder="时间范围"
          size="small"
          style="width: 130px"
          @update:value="load"
        />
        <n-select
          v-model:value="filter.status"
          :options="statusOpts"
          placeholder="状态"
          size="small"
          clearable
          style="width: 140px"
          @update:value="load"
        />
        <n-select
          v-model:value="filter.stream"
          :options="streamOpts"
          placeholder="流式"
          size="small"
          clearable
          style="width: 130px"
          @update:value="load"
        />
        <n-select
          v-model:value="filter.model"
          :options="modelOpts"
          placeholder="模型"
          size="small"
          clearable
          filterable
          style="width: 200px"
          @update:value="load"
        />
        <n-select
          v-model:value="filter.upstream_key_id"
          :options="upstreamOpts"
          placeholder="上游 Key"
          size="small"
          clearable
          style="width: 200px"
          @update:value="load"
        />
        <n-select
          v-model:value="filter.api_key_id"
          :options="apiKeyOpts"
          placeholder="API Key"
          size="small"
          clearable
          filterable
          style="width: 200px"
          @update:value="load"
        />
        <n-input
          v-model:value="filter.request_id"
          placeholder="Request ID"
          size="small"
          clearable
          style="width: 220px"
          @keyup.enter="load"
        />
        <n-button size="small" type="primary" @click="load" :loading="loading">查询</n-button>
        <n-button size="small" @click="resetFilter">重置</n-button>
        <n-popconfirm @positive-click="onDeleteByFilter" :show-icon="false">
          <template #trigger>
            <n-button size="small" type="warning" ghost :disabled="!hasAnyFilter">
              按筛选删除
            </n-button>
          </template>
          按当前筛选删除调用日志？此操作不可撤销。
        </n-popconfirm>
        <n-popconfirm @positive-click="onDeleteErrors" :show-icon="false">
          <template #trigger>
            <n-button size="small" type="error" ghost>
              清理失败 (4xx/5xx)
            </n-button>
          </template>
          确定删除所有 status_code ≥ 400 的调用日志？
        </n-popconfirm>
      </div>
    </n-card>

    <!-- 表格 -->
    <n-data-table
      :columns="columns"
      :data="rows"
      :loading="loading"
      size="small"
      :scroll-x="1700"
      :row-key="(r) => r.id"
      :pagination="{ pageSize: 20, showSizePicker: true, pageSizes: [10, 20, 50, 100] }"
      :bordered="false"
    />
  </n-space>
</template>

<script setup>
import { ref, h, onMounted, reactive, computed } from 'vue'
import { NTag, NSpace, NDescriptions, NDescriptionsItem, NCode, NDivider, NPopconfirm, NTooltip, NButton, useMessage } from 'naive-ui'
import { logsApi, modelApi, upstreamApi, apiKeysApi } from '@/api'
import dayjs from 'dayjs'

const message = useMessage()

function copyToClipboard(text) {
  if (!text) return
  // 优先 navigator.clipboard（HTTPS/localhost）
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text)
      .then(() => message.success('已复制到剪贴板'))
      .catch(() => message.error('复制失败'))
    return
  }
  // fallback for http(非 localhost)：execCommand
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  try {
    document.execCommand('copy')
    message.success('已复制到剪贴板')
  } catch {
    message.error('复制失败')
  } finally {
    document.body.removeChild(ta)
  }
}

const rows = ref([])
const loading = ref(false)
const modelOpts = ref([])
const upstreamOpts = ref([])
const apiKeyOpts = ref([])

const filter = reactive({
  period: '24h',
  status: null,
  stream: null,
  model: null,
  upstream_key_id: null,
  api_key_id: null,
  request_id: '',
})

const periodOpts = [
  { label: '最近 1 小时', value: '1h' },
  { label: '最近 24 小时', value: '24h' },
  { label: '最近 7 天', value: '7d' },
  { label: '全部', value: 'all' },
]
const statusOpts = [
  { label: '✓ 成功 (2xx)', value: '2xx' },
  { label: '⚠ 客户端错误 (4xx)', value: '4xx' },
  { label: '✗ 服务端错误 (5xx)', value: '5xx' },
]
const streamOpts = [
  { label: '🌊 流式', value: 'true' },
  { label: '一次性', value: 'false' },
]

function resetFilter() {
  filter.period = '24h'
  filter.status = null
  filter.stream = null
  filter.model = null
  filter.upstream_key_id = null
  filter.api_key_id = null
  filter.request_id = ''
  load()
}

const hasAnyFilter = computed(() =>
  Boolean(filter.status || filter.stream || filter.model || filter.upstream_key_id ||
          filter.api_key_id || filter.request_id ||
          (filter.period && filter.period !== 'all'))
)

function periodToAfter(p) {
  const now = new Date()
  if (p === '1h') return new Date(now.getTime() - 3600_000).toISOString()
  if (p === '24h') return new Date(now.getTime() - 86400_000).toISOString()
  if (p === '7d') return new Date(now.getTime() - 7 * 86400_000).toISOString()
  return null
}

async function onDeleteByFilter() {
  const f = {}
  // status -> status_code_gte（4xx/5xx 一律删 >= 边界）
  if (filter.status === '4xx') f.status_code_gte = 400
  if (filter.status === '5xx') f.status_code_gte = 500
  if (filter.upstream_key_id) f.upstream_key_id = filter.upstream_key_id
  if (filter.api_key_id) f.api_key_id = filter.api_key_id
  const after = periodToAfter(filter.period)
  if (after) f.after = after
  // status='2xx' 没法直接表达（API 只支持 >= 阈值），提示用户
  if (filter.status === '2xx') {
    message.warning('当前 API 只支持按 status_code ≥ 阈值删除，2xx 暂不支持按筛选删')
    return
  }
  try {
    const { data } = await logsApi.requestBulkDelete(f)
    if (data.error) {
      message.warning(data.error)
      return
    }
    message.success(`已删除 ${data.deleted} 条调用日志`)
    load()
  } catch (e) {
    message.error(`删除失败：${e?.message || e}`)
  }
}

async function onDeleteErrors() {
  try {
    const { data } = await logsApi.requestBulkDelete({ status_code_gte: 400 })
    message.success(`已删除 ${data.deleted} 条失败请求`)
    load()
  } catch (e) {
    message.error(`删除失败：${e?.message || e}`)
  }
}

const codeType = (c) => c >= 500 ? 'error' : c >= 400 ? 'warning' : c >= 200 ? 'success' : 'default'

// 给不同模型/端点生成一致的颜色（hash 法）
function hashColor(str) {
  if (!str) return { color: '#94a3b8', textColor: '#fff' }
  const colors = [
    { color: 'rgba(20,184,166,0.14)',  textColor: '#0d9488' },
    { color: 'rgba(59,130,246,0.14)',  textColor: '#2563eb' },
    { color: 'rgba(168,85,247,0.14)',  textColor: '#9333ea' },
    { color: 'rgba(34,197,94,0.14)',   textColor: '#16a34a' },
    { color: 'rgba(245,158,11,0.14)',  textColor: '#d97706' },
    { color: 'rgba(244,63,94,0.14)',   textColor: '#e11d48' },
    { color: 'rgba(139,92,246,0.14)',  textColor: '#7c3aed' },
    { color: 'rgba(14,165,233,0.14)',  textColor: '#0284c7' },
    { color: 'rgba(99,102,241,0.14)',  textColor: '#4f46e5' },
    { color: 'rgba(16,185,129,0.14)',  textColor: '#059669' },
  ]
  let h = 0
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) & 0x7fffffff
  return colors[h % colors.length]
}

// 根据模型名前缀返回 emoji（视觉提示）
function modelEmoji(name) {
  if (!name) return '🤖'
  const n = name.toLowerCase()
  if (n.includes('gpt')) return '⊕'
  if (n.includes('claude')) return '💧'
  if (n.includes('gemini')) return '✦'
  if (n.includes('kimi')) return '🌙'
  if (n.includes('deepseek')) return '🪐'
  if (n.includes('glm')) return '🔷'
  if (n.includes('qwen')) return '🐦'
  if (n.includes('flux')) return '🎨'
  if (n.includes('embed')) return '📐'
  if (n.includes('rerank')) return '🔀'
  return '🤖'
}

// 状态业务化（消费 / 客户端错误 / 服务端错误 / 限流）
function statusBadge(r) {
  const c = r.status_code
  if (c >= 200 && c < 300) return { label: '消费', type: 'success' }
  if (c === 429) return { label: '限流', type: 'warning' }
  if (c >= 400 && c < 500) return { label: '客户端错误', type: 'warning' }
  if (c >= 500) return { label: '服务端错误', type: 'error' }
  if (c === 0) return { label: '网络异常', type: 'error' }
  return { label: `HTTP ${c}`, type: 'default' }
}

const columns = computed(() => [
  { type: 'expand', renderExpand: renderExpand, expandable: () => true },
  { title: '时间', key: 'created_at', width: 165,
    render: (r) => h('div', { style: 'font-size:12px;line-height:1.3' }, [
      h('div', null, dayjs(r.created_at).format('MM-DD HH:mm:ss')),
      h('div', { style: 'color:var(--n-text-color-3,#94a3b8);font-size:11px' }, dayjs(r.created_at).fromNow ? dayjs(r.created_at).fromNow() : ''),
    ]),
  },
  {
    title: 'API Key', key: 'api_key_label', width: 180, ellipsis: { tooltip: true },
    render: (r) => h('div', { style: 'line-height:1.3' }, [
      h(NTag, { size: 'small', bordered: false, color: hashColor(r.api_key_label) },
        () => r.api_key_label || '-'),
      h('div', { style: 'font-size:10.5px;color:var(--n-text-color-3,#94a3b8);font-family:ui-monospace,Menlo,monospace;margin-top:2px' },
        r.api_key_preview || ''),
    ]),
  },
  {
    title: '端点', key: 'endpoint', width: 140,
    render: (r) => h(NTag, { size: 'small', bordered: false, color: { color: 'rgba(13,148,136,0.12)', textColor: '#0d9488' } },
      () => r.endpoint),
  },
  {
    title: '模型', key: 'public_model', width: 220, ellipsis: { tooltip: true },
    render: (r) => h(NTag, { size: 'small', bordered: false, color: hashColor(r.public_model) },
      () => `${modelEmoji(r.public_model)} ${r.public_model}`),
  },
  {
    title: '上游 Key', key: 'upstream_key_preview', width: 150,
    render: (r) => r.upstream_key_id
      ? h(NTag, { size: 'small', bordered: false, color: { color: 'rgba(99,102,241,0.12)', textColor: '#4f46e5' } },
        () => `#${r.upstream_key_id} ${r.upstream_key_preview || ''}`)
      : '-',
  },
  {
    title: '类型 / 错误', key: 'status_code', width: 260,
    render: (r) => {
      const b = statusBadge(r)
      const tag = h(NTag, { size: 'small', type: b.type, bordered: false }, () => b.label)
      const hasErr = !!(r.error_code || r.error_message)
      if (!hasErr) {
        return tag
      }
      // 失败请求：标签 + 错误码/前 80 字符 message 直接展示，hover 看完整 detail
      const codeChip = r.error_code
        ? h(NTag, { size: 'tiny', bordered: false, type: 'error', style: 'margin-left:4px' },
            () => r.error_code)
        : null
      const msgPreview = r.error_message
        ? String(r.error_message).slice(0, 80) + (r.error_message.length > 80 ? '…' : '')
        : ''
      return h('div', { style: 'line-height:1.4' }, [
        h(NSpace, { size: 4, wrap: false, align: 'center' }, () => [tag, codeChip].filter(Boolean)),
        msgPreview
          ? h(NTooltip, { placement: 'top', style: 'max-width:560px' }, {
              trigger: () => h('div', {
                style: 'margin-top:3px;font-size:11.5px;color:#dc2626;cursor:help;' +
                       'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:240px',
              }, msgPreview),
              default: () => h('pre', {
                style: 'margin:0;font-size:11.5px;white-space:pre-wrap;word-break:break-all;' +
                       'max-height:360px;overflow:auto;line-height:1.5',
              }, r.error_message),
            })
          : null,
      ].filter(Boolean))
    },
  },
  {
    title: '用时 / 首字', key: 'latency', width: 150,
    render: (r) => h(NSpace, { size: 4, align: 'center' }, () => [
      h(NTag, { size: 'small', bordered: false, color: { color: 'rgba(20,184,166,0.14)', textColor: '#0d9488' } },
        () => `${r.latency_ms}ms`),
      r.ttft_ms > 0
        ? h(NTag, { size: 'small', bordered: false, color: { color: 'rgba(245,158,11,0.14)', textColor: '#d97706' } },
            () => `首字 ${r.ttft_ms}ms`)
        : null,
      r.stream
        ? h(NTag, { size: 'tiny', bordered: false, type: 'info' }, () => '🌊 流')
        : h(NTag, { size: 'tiny', bordered: false }, () => '非流'),
    ].filter(Boolean)),
  },
  {
    title: '详情', key: 'cost', width: 230,
    render: (r) => h('div', { style: 'font-size:11.5px;line-height:1.5;color:var(--n-text-color-2,#475569)' }, [
      h('div', { style: 'font-weight:600;color:#0d9488' },
        `账单 $${(r.billed_cost_usd ?? 0).toFixed(6)}${r.rate_multiplier !== 1 ? ` (×${r.rate_multiplier})` : ''}`),
      h('div', null, `输入 ${r.prompt_tokens} · 输出 ${r.completion_tokens}${r.cached_tokens ? ` · 缓存 ${r.cached_tokens}` : ''}`),
      r.retry_count
        ? h('div', { style: 'color:#f59e0b' }, `重试 ${r.retry_count} 次`)
        : null,
    ].filter(Boolean)),
  },
])

function renderExpand(r) {
  // 计费过程拆分
  const inputCost = r.prompt_tokens / 1_000_000 * (r.raw_cost_usd && r.prompt_tokens ? (r.raw_cost_usd - r.completion_tokens / 1_000_000 * 0) / (r.prompt_tokens / 1_000_000) : 0)
  const formula = renderBillFormula(r)

  return h('div', { class: 'log-expand' }, [
    h(NDescriptions, { column: 3, bordered: true, size: 'small', labelPlacement: 'left', labelStyle: 'width: 90px' }, () => [
      h(NDescriptionsItem, { label: 'Request ID' }, () =>
        h('code', { style: 'font-size:11.5px;color:#475569' }, r.request_id)),
      h(NDescriptionsItem, { label: '请求路径' }, () =>
        h('code', { style: 'font-size:11.5px;color:#0d9488' }, r.endpoint)),
      h(NDescriptionsItem, { label: '上游 Key' }, () =>
        r.upstream_key_id
          ? `#${r.upstream_key_id} ${r.upstream_key_preview || ''}`
          : '-'),
      h(NDescriptionsItem, { label: '流状态' }, () =>
        r.stream
          ? h('span', { style: 'color:#0d9488' }, `🌊 流式（首字 ${r.ttft_ms}ms · 完成 ${r.latency_ms}ms）`)
          : h('span', null, `一次性（${r.latency_ms}ms）`)),
      h(NDescriptionsItem, { label: 'HTTP 状态' }, () =>
        h(NTag, { size: 'small', type: codeType(r.status_code), bordered: false }, () => r.status_code)),
      h(NDescriptionsItem, { label: '重试次数' }, () => r.retry_count || 0),
      h(NDescriptionsItem, { label: 'Token 用量', span: 3 }, () =>
        h('span', null, `输入 ${r.prompt_tokens} tokens · 输出 ${r.completion_tokens} tokens${r.cached_tokens ? ` · 缓存命中 ${r.cached_tokens} tokens` : ''} · 合计 ${r.total_tokens || (r.prompt_tokens + r.completion_tokens)} tokens`)),
      h(NDescriptionsItem, { label: '计费过程', span: 3 }, () => formula),
      r.error_code || r.error_message
        ? h(NDescriptionsItem, { label: '错误详情', span: 3 }, () =>
            h('div', {
              style: 'background:rgba(239,68,68,0.06);border-left:3px solid #ef4444;' +
                     'border-radius:4px;padding:8px 10px',
            }, [
              h('div', { style: 'display:flex;align-items:center;gap:8px;margin-bottom:6px' }, [
                r.error_code
                  ? h(NTag, { size: 'small', type: 'error', bordered: false }, () => r.error_code)
                  : null,
                h('span', { style: 'color:#ef4444;font-weight:600;font-size:12px' },
                  `HTTP ${r.status_code}`),
                r.error_message
                  ? h(NButton, {
                      size: 'tiny', text: true, type: 'error',
                      onClick: () => copyToClipboard(r.error_message),
                    }, () => '📋 复制')
                  : null,
              ].filter(Boolean)),
              r.error_message
                ? h('pre', {
                    style: 'margin:0;font-size:11.5px;line-height:1.55;color:#7f1d1d;' +
                           'white-space:pre-wrap;word-break:break-all;' +
                           'max-height:400px;overflow:auto;font-family:ui-monospace,Menlo,monospace',
                  }, r.error_message)
                : h('span', { style: 'color:#94a3b8;font-size:12px' }, '(无详细信息)'),
            ]))
        : null,
    ].filter(Boolean)),
  ])
}

function renderBillFormula(r) {
  // 计算每段
  const prompt = r.prompt_tokens || 0
  const completion = r.completion_tokens || 0
  const cached = r.cached_tokens || 0
  const billable = Math.max(0, prompt - cached)
  const raw = r.raw_cost_usd || 0
  const billed = r.billed_cost_usd || 0
  const mult = r.rate_multiplier || 1

  return h('div', { style: 'font-family:ui-monospace,Menlo,monospace;font-size:11.5px;line-height:1.7;color:#475569' }, [
    h('div', null, `输入计费: ${billable} tokens · 输出: ${completion} tokens${cached ? ` · 缓存命中: ${cached} tokens` : ''}`),
    h('div', null, `上游原始成本: $${raw.toFixed(7)}`),
    mult !== 1
      ? h('div', null, `专属倍率 × ${mult} → 账单成本: $${billed.toFixed(7)}`)
      : h('div', null, `账单成本: $${billed.toFixed(7)} (1×)`),
  ])
}

async function load() {
  loading.value = true
  try {
    const params = { limit: 200 }
    // 时间筛选传给后端（约定走 created_at >= now-N）
    if (filter.period && filter.period !== 'all') params.period = filter.period
    if (filter.upstream_key_id) params.upstream_key_id = filter.upstream_key_id
    if (filter.api_key_id) params.api_key_id = filter.api_key_id
    if (filter.request_id) params.request_id = filter.request_id
    if (filter.model) params.model = filter.model
    if (filter.stream !== null) params.stream = filter.stream
    // status 段
    if (filter.status === '2xx') params.status_min = 200
    if (filter.status === '4xx') { params.status_min = 400; params.status_max = 499 }
    if (filter.status === '5xx') params.status_min = 500
    const { data } = await logsApi.requests(params)
    rows.value = data.items
  } finally { loading.value = false }
}

async function loadFilterOptions() {
  try {
    const [m, u, k] = await Promise.all([
      modelApi.list(), upstreamApi.list(), apiKeysApi.list(),
    ])
    modelOpts.value = (m.data || [])
      .filter((x) => x.status === 'active')
      .map((x) => ({ label: x.public_name, value: x.public_name }))
    upstreamOpts.value = (u.data || []).map((x) => ({
      label: `#${x.id} ${x.name} (${x.key_preview})`, value: x.id,
    }))
    apiKeyOpts.value = (k.data || []).map((x) => ({
      label: `${x.label} (${x.token_preview})`, value: x.id,
    }))
  } catch (_) { /* ignore */ }
}

onMounted(() => {
  loadFilterOptions()
  load()
})
</script>

<style scoped>
.filter-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.log-expand {
  padding: 12px 20px;
  background: var(--n-color-target, rgba(248, 250, 252, 0.6));
  border-radius: 8px;
  margin: 8px 16px;
}
</style>
