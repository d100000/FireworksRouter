<template>
  <n-space vertical>
    <n-space>
      <n-button @click="load">刷新</n-button>
      <n-input v-model:value="filter.upstream_key_id" placeholder="上游 Key ID" clearable style="width: 140px" />
      <n-input v-model:value="filter.user_token_id" placeholder="Token ID" clearable style="width: 140px" />
      <n-input v-model:value="filter.status_code" placeholder="状态码" clearable style="width: 120px" />
      <n-button type="primary" size="small" @click="load">查询</n-button>
    </n-space>
    <n-data-table :columns="columns" :data="rows" :loading="loading" size="small" :scroll-x="1800" />
  </n-space>
</template>

<script setup>
import { ref, h, onMounted, reactive } from 'vue'
import { NTag } from 'naive-ui'
import { logsApi } from '@/api'
import dayjs from 'dayjs'

const rows = ref([]); const loading = ref(false)
const filter = reactive({ upstream_key_id: '', user_token_id: '', status_code: '' })

const codeType = (c) => c >= 500 ? 'error' : c >= 400 ? 'warning' : c >= 200 ? 'success' : 'default'

const columns = [
  { title: 'ID', key: 'id', width: 70 },
  { title: '时间', key: 'created_at', width: 170,
    render: (r) => dayjs(r.created_at).format('YYYY-MM-DD HH:mm:ss') },
  { title: '用户', key: 'owner', width: 200 },
  { title: '模型', key: 'public_model', width: 220, ellipsis: { tooltip: true } },
  { title: '上游 Key', key: 'upstream_key_preview', width: 140 },
  {
    title: '状态', key: 'status_code', width: 80,
    render: (r) => h(NTag, { size: 'small', type: codeType(r.status_code) }, () => r.status_code),
  },
  { title: '流式', key: 'stream', width: 60,
    render: (r) => r.stream ? '✓' : '' },
  { title: 'Prompt', key: 'prompt_tokens', width: 80 },
  { title: 'Comp', key: 'completion_tokens', width: 80 },
  { title: 'Cached', key: 'cached_tokens', width: 80 },
  { title: 'TTFT', key: 'ttft_ms', width: 80,
    render: (r) => r.ttft_ms ? `${r.ttft_ms}ms` : '-' },
  { title: '延迟', key: 'latency_ms', width: 90,
    render: (r) => `${r.latency_ms}ms` },
  { title: '原始成本', key: 'raw_cost_usd', width: 110,
    render: (r) => `$${(r.raw_cost_usd ?? 0).toFixed(6)}` },
  { title: '账单成本', key: 'billed_cost_usd', width: 110,
    render: (r) => `$${(r.billed_cost_usd ?? 0).toFixed(6)}` },
  { title: '重试', key: 'retry_count', width: 60 },
  { title: 'request_id', key: 'request_id', width: 230 },
]

async function load() {
  loading.value = true
  try {
    const params = { limit: 100 }
    for (const k of ['upstream_key_id', 'user_token_id', 'status_code']) {
      if (filter[k]) params[k] = Number(filter[k])
    }
    const { data } = await logsApi.requests(params)
    rows.value = data.items
  } finally { loading.value = false }
}
onMounted(load)
</script>
