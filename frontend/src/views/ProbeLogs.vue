<template>
  <n-space vertical>
    <n-button @click="load">刷新</n-button>
    <n-data-table :columns="columns" :data="rows" :loading="loading" size="small" :scroll-x="1200" />
  </n-space>
</template>

<script setup>
import { ref, h, onMounted } from 'vue'
import { NTag } from 'naive-ui'
import { logsApi } from '@/api'
import dayjs from 'dayjs'

const rows = ref([]); const loading = ref(false)

const columns = [
  { title: 'ID', key: 'id', width: 70 },
  { title: '时间', key: 'created_at', width: 170,
    render: (r) => dayjs(r.created_at).format('YYYY-MM-DD HH:mm:ss') },
  { title: '上游 Key ID', key: 'upstream_key_id', width: 100 },
  { title: 'Key', key: 'upstream_key_preview', width: 160 },
  {
    title: '结果', key: 'success', width: 80,
    render: (r) => h(NTag, { size: 'small', type: r.success === 'ok' ? 'success' : 'error' }, () => r.success),
  },
  { title: '余额', key: 'balance_usd', width: 110,
    render: (r) => `$${r.balance_usd?.toFixed(4)}` },
  { title: '月限额', key: 'monthly_spend_limit_usd', width: 100,
    render: (r) => `$${r.monthly_spend_limit_usd?.toFixed(2)}` },
  { title: '本月已用', key: 'monthly_spend_used_usd', width: 100,
    render: (r) => `$${r.monthly_spend_used_usd?.toFixed(4)}` },
  { title: 'suspend', key: 'suspend_state', width: 120 },
  { title: 'account', key: 'account_state', width: 100 },
  { title: '延迟', key: 'latency_ms', width: 90,
    render: (r) => `${r.latency_ms}ms` },
  { title: '错误', key: 'error_message', ellipsis: { tooltip: true } },
]

async function load() {
  loading.value = true
  try {
    const { data } = await logsApi.probes({ limit: 100 })
    rows.value = data.items
  } finally { loading.value = false }
}
onMounted(load)
</script>
