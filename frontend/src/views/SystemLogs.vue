<template>
  <n-space vertical size="large">
    <!-- 顶部筛选条 -->
    <n-card size="small" :bordered="false">
      <div class="filter-row">
        <n-select
          v-model:value="filter.level"
          :options="levelOpts"
          placeholder="级别"
          size="small"
          clearable
          style="width: 140px"
          @update:value="load"
        />
        <n-input
          v-model:value="filter.module"
          placeholder="模块（模糊）"
          size="small"
          clearable
          style="width: 180px"
          @keyup.enter="load"
        />
        <n-input
          v-model:value="filter.search"
          placeholder="搜索消息..."
          size="small"
          clearable
          style="width: 220px"
          @keyup.enter="load"
        />
        <n-input
          v-model:value="filter.request_id"
          placeholder="request_id"
          size="small"
          clearable
          style="width: 200px"
          @keyup.enter="load"
        />
        <n-select
          v-model:value="filter.period"
          :options="periodOpts"
          placeholder="时间范围"
          size="small"
          style="width: 130px"
          @update:value="load"
        />
        <n-button size="small" @click="load" :loading="loading">刷新</n-button>
        <n-button size="small" type="warning" ghost @click="onClearOld" :loading="cleanLoading">
          清理过期
        </n-button>
        <n-popconfirm @positive-click="onDeleteFiltered" :show-icon="false">
          <template #trigger>
            <n-button size="small" type="error" ghost :disabled="!hasAnyFilter">
              按当前筛选删除
            </n-button>
          </template>
          确定按当前筛选条件批量删除？此操作不可撤销。
        </n-popconfirm>
      </div>
      <div class="meta-line">
        共 <b>{{ total }}</b> 条 ·
        最近一次清理删除：app={{ lastCleanup.system_logs }}/req={{ lastCleanup.request_logs }}/probe={{ lastCleanup.probe_history }}/桶={{ lastCleanup.metric_buckets }}
      </div>
    </n-card>

    <!-- 表格 -->
    <n-card :bordered="false">
      <n-data-table
        :columns="columns"
        :data="items"
        :loading="loading"
        :pagination="pagination"
        :row-key="(row) => row.id"
        :checked-row-keys="checkedIds"
        @update:checked-row-keys="(v) => (checkedIds = v)"
        striped
        size="small"
      />
      <div v-if="checkedIds.length" class="batch-bar">
        <span>已选 {{ checkedIds.length }} 条</span>
        <n-popconfirm @positive-click="onDeleteChecked" :show-icon="false">
          <template #trigger>
            <n-button size="small" type="error">删除选中</n-button>
          </template>
          删除选中 {{ checkedIds.length }} 条日志？
        </n-popconfirm>
      </div>
    </n-card>
  </n-space>
</template>

<script setup>
import { computed, h, onMounted, reactive, ref } from 'vue'
import {
  NSpace, NCard, NSelect, NInput, NButton, NDataTable,
  NPopconfirm, NTag, NTooltip, NEllipsis, useMessage,
} from 'naive-ui'
import { logsApi } from '@/api'

const message = useMessage()

const loading = ref(false)
const cleanLoading = ref(false)
const items = ref([])
const total = ref(0)
const checkedIds = ref([])
const lastCleanup = ref({ system_logs: 0, request_logs: 0, probe_history: 0, metric_buckets: 0 })

const filter = reactive({
  level: null,
  module: '',
  search: '',
  request_id: '',
  period: '24h',
})

const hasAnyFilter = computed(() =>
  filter.level || filter.module || filter.search || filter.request_id,
)

const levelOpts = [
  { label: 'DEBUG', value: 'DEBUG' },
  { label: 'INFO', value: 'INFO' },
  { label: 'WARNING', value: 'WARNING' },
  { label: 'ERROR', value: 'ERROR' },
  { label: 'CRITICAL', value: 'CRITICAL' },
]

const periodOpts = [
  { label: '1 小时', value: '1h' },
  { label: '24 小时', value: '24h' },
  { label: '7 天', value: '7d' },
  { label: '全部', value: 'all' },
]

const pagination = reactive({
  page: 1,
  pageSize: 50,
  pageSizes: [20, 50, 100, 200],
  showSizePicker: true,
  itemCount: 0,
  onChange: (page) => {
    pagination.page = page
    load()
  },
  onUpdatePageSize: (size) => {
    pagination.pageSize = size
    pagination.page = 1
    load()
  },
})

function periodToStart(p) {
  const now = new Date()
  if (p === '1h') return new Date(now.getTime() - 3600_000).toISOString()
  if (p === '24h') return new Date(now.getTime() - 86400_000).toISOString()
  if (p === '7d') return new Date(now.getTime() - 7 * 86400_000).toISOString()
  return null
}

async function load() {
  loading.value = true
  try {
    const params = {
      limit: pagination.pageSize,
      offset: (pagination.page - 1) * pagination.pageSize,
    }
    if (filter.level) params.level = filter.level
    if (filter.module) params.module = filter.module
    if (filter.search) params.search = filter.search
    if (filter.request_id) params.request_id = filter.request_id
    const start = periodToStart(filter.period)
    if (start) params.start = start

    const { data } = await logsApi.systemList(params)
    items.value = data.items || []
    total.value = data.total || 0
    pagination.itemCount = total.value
    checkedIds.value = []
  } catch (e) {
    message.error(`加载失败：${e?.message || e}`)
  } finally {
    loading.value = false
  }
}

async function onClearOld() {
  cleanLoading.value = true
  try {
    const { data } = await logsApi.cleanupRunNow()
    lastCleanup.value = data.deleted || {}
    message.success(`清理完成：系统日志 ${data.deleted?.system_logs || 0} 条`)
    load()
  } catch (e) {
    message.error(`清理失败：${e?.message || e}`)
  } finally {
    cleanLoading.value = false
  }
}

async function onDeleteFiltered() {
  const filt = {}
  if (filter.level) filt.level = filter.level
  if (filter.module) filt.module = filter.module
  if (filter.search) filt.search = filter.search
  if (filter.request_id) filt.request_id = filter.request_id
  const start = periodToStart(filter.period)
  if (start) filt.after = start
  try {
    const { data } = await logsApi.systemBulkDelete(filt)
    if (data.error) {
      message.warning(data.error)
      return
    }
    message.success(`已删除 ${data.deleted} 条`)
    load()
  } catch (e) {
    message.error(`删除失败：${e?.message || e}`)
  }
}

async function onDeleteChecked() {
  try {
    const { data } = await logsApi.systemBulkDelete({ ids: checkedIds.value })
    message.success(`已删除 ${data.deleted} 条`)
    load()
  } catch (e) {
    message.error(`删除失败：${e?.message || e}`)
  }
}

function levelTag(level) {
  const map = {
    DEBUG: { type: 'default', label: 'DEBUG' },
    INFO: { type: 'info', label: 'INFO' },
    WARNING: { type: 'warning', label: 'WARN' },
    ERROR: { type: 'error', label: 'ERROR' },
    CRITICAL: { type: 'error', label: 'CRIT' },
  }
  const m = map[level] || { type: 'default', label: level }
  return h(NTag, { type: m.type, size: 'small', bordered: false }, () => m.label)
}

function fmtTime(s) {
  if (!s) return '—'
  const d = new Date(s)
  return d.toLocaleString('zh-CN', { hour12: false })
}

const columns = [
  { type: 'selection', width: 40 },
  {
    title: '时间',
    key: 'timestamp',
    width: 180,
    render: (row) => fmtTime(row.timestamp),
  },
  {
    title: '级别',
    key: 'level',
    width: 80,
    render: (row) => levelTag(row.level),
  },
  {
    title: '来源',
    key: 'module',
    width: 260,
    render: (row) =>
      h(
        NTooltip,
        {},
        {
          trigger: () =>
            h('span', { class: 'mono small muted' }, `${row.module}:${row.line}`),
          default: () => `${row.module}.${row.function}:${row.line}`,
        },
      ),
  },
  {
    title: '消息',
    key: 'message',
    render: (row) =>
      h(NEllipsis, { tooltip: { width: 600 }, lineClamp: 2 }, () => row.message || ''),
  },
  {
    title: 'request_id',
    key: 'request_id',
    width: 180,
    render: (row) =>
      row.request_id
        ? h('span', { class: 'mono small muted' }, row.request_id)
        : '—',
  },
  {
    title: '详情',
    key: 'extra',
    width: 80,
    render: (row) => {
      if (!row.extra) return '—'
      let parsed = null
      try {
        parsed = JSON.parse(row.extra)
      } catch {
        return '⚠️'
      }
      return h(
        NTooltip,
        { trigger: 'click', width: 600 },
        {
          trigger: () =>
            h(
              NButton,
              { text: true, size: 'small' },
              () => (parsed.exception ? '🔥 异常' : '📋 上下文'),
            ),
          default: () =>
            h('pre', { class: 'extra-pre' }, JSON.stringify(parsed, null, 2)),
        },
      )
    },
  },
]

onMounted(load)
</script>

<style scoped>
.filter-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.meta-line {
  margin-top: 8px;
  font-size: 12px;
  color: var(--n-text-color-3, #888);
}
.batch-bar {
  margin-top: 12px;
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 8px 12px;
  background: var(--n-color-info, rgba(64, 158, 255, 0.08));
  border-radius: 6px;
}
:deep(.mono) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
:deep(.small) {
  font-size: 12px;
}
:deep(.muted) {
  color: var(--n-text-color-3, #888);
}
.extra-pre {
  margin: 0;
  font-size: 11px;
  line-height: 1.45;
  max-height: 400px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
