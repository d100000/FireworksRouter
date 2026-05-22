<template>
  <n-space vertical>
    <n-space justify="space-between">
      <n-button type="primary" @click="openAdd">新建 API Key</n-button>
      <n-button @click="load">刷新</n-button>
    </n-space>
    <n-data-table :columns="columns" :data="rows" :loading="loading" size="small" :scroll-x="1400" />

    <n-modal v-model:show="editShow" preset="card" title="新建 API Key" style="width: 520px">
      <n-form :model="form" label-placement="top">
        <n-form-item label="标签（label）" required>
          <n-input v-model:value="form.label" placeholder="如 production-app / team-x" />
        </n-form-item>
        <n-form-item label="备注（note，可选）">
          <n-input v-model:value="form.note" type="textarea" :rows="2" />
        </n-form-item>
        <n-form-item label="允许流式">
          <n-switch v-model:value="form.stream_enabled" />
        </n-form-item>
        <n-form-item label="单次最大 max_tokens（0=不限）">
          <n-input-number v-model:value="form.max_tokens_per_request" :min="0" style="width:100%" />
        </n-form-item>
        <n-form-item label="允许的模型（不填=全部）">
          <n-dynamic-tags v-model:value="form.allowed_models" />
        </n-form-item>
        <n-form-item label="无限额度">
          <n-switch v-model:value="form.unlimited_quota" />
        </n-form-item>
        <n-form-item label="预付额度（USD）" v-if="!form.unlimited_quota">
          <n-input-number v-model:value="form.remaining_quota_usd" :precision="4" :min="0" style="width:100%" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="editShow = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="submit">创建</n-button>
        </n-space>
      </template>
    </n-modal>

    <n-modal v-model:show="createdShow" preset="card" title="API Key — 仅一次显示" style="width: 560px">
      <n-alert type="warning" style="margin-bottom: 12px">这是新的 API Key，请立即保存。关闭后将无法再次查看。</n-alert>
      <n-input :value="createdToken" readonly type="textarea" :autosize="{ minRows: 2 }" />
      <template #footer>
        <n-space justify="end">
          <n-button type="primary" @click="copyCreatedToken">复制</n-button>
          <n-button @click="createdShow = false">关闭</n-button>
        </n-space>
      </template>
    </n-modal>
  </n-space>
</template>

<script setup>
import { ref, h, onMounted, reactive } from 'vue'
import { NTag, NButton, NPopconfirm, NSpace, useMessage } from 'naive-ui'
import { apiKeysApi } from '@/api'
import StatusDot from '@/components/StatusDot.vue'

const message = useMessage()
const rows = ref([])
const loading = ref(false)
const editShow = ref(false)
const saving = ref(false)
const form = reactive({
  label: '', note: '', stream_enabled: true,
  max_tokens_per_request: 0, allowed_models: [],
  unlimited_quota: true, remaining_quota_usd: 0,
})

const createdShow = ref(false)
const createdToken = ref('')

const columns = [
  { title: 'ID', key: 'id', width: 60 },
  { title: '标签', key: 'label', width: 180 },
  { title: 'Token', key: 'token_preview', width: 220, render: (r) => h('code', { style: 'font-size:12px;color:var(--n-text-color-3,#64748b)' }, r.token_preview) },
  {
    title: '状态', key: 'status', width: 100,
    render: (r) => h(StatusDot, { status: r.status, label: r.status, pulse: r.status === 'active' }),
  },
  { title: '请求数', key: 'total_requests', width: 80 },
  {
    title: '配额', key: 'quota', width: 180,
    render: (r) => r.unlimited_quota
      ? h(NTag, { size: 'small', type: 'info' }, () => '无限')
      : `已用 $${(r.used_quota_usd ?? 0).toFixed(5)} / 剩 $${(r.remaining_quota_usd ?? 0).toFixed(5)}`,
  },
  {
    title: '允许模型', key: 'allowed_models', width: 200,
    render: (r) => (r.allowed_models?.length || 0)
      ? r.allowed_models.slice(0, 2).join(', ') + (r.allowed_models.length > 2 ? '...' : '')
      : '全部',
  },
  { title: '最后使用', key: 'last_used_at', width: 170 },
  {
    title: '操作', key: 'actions', width: 280, fixed: 'right',
    render: (row) => h(NSpace, { size: 'small' }, () => [
      // 复制完整 token（v4 之前的旧 key 无法直接复制，需要先 rotate）
      row.can_reveal
        ? h(NButton, { size: 'tiny', type: 'primary', ghost: true, onClick: () => copyToken(row.id) }, () => '复制')
        : h(NButton, { size: 'tiny', disabled: true, title: '旧 Key 无密文，请旋转后再复制' }, () => '复制(需旋转)'),
      h(NButton, {
        size: 'tiny', type: row.status === 'active' ? 'warning' : 'primary',
        onClick: () => toggleStatus(row),
      }, () => row.status === 'active' ? '禁用' : '启用'),
      h(NPopconfirm, { onPositiveClick: () => rotate(row.id) }, {
        default: () => '旋转此 Key？旧的立即失效',
        trigger: () => h(NButton, { size: 'tiny' }, () => '旋转'),
      }),
      h(NPopconfirm, { onPositiveClick: () => remove(row.id) }, {
        default: () => '删除该 Key？',
        trigger: () => h(NButton, { size: 'tiny', type: 'error' }, () => '删除'),
      }),
    ]),
  },
]

async function load() {
  loading.value = true
  try {
    const { data } = await apiKeysApi.list()
    rows.value = data
  } finally { loading.value = false }
}

function openAdd() {
  Object.assign(form, {
    label: '', note: '', stream_enabled: true,
    max_tokens_per_request: 0, allowed_models: [],
    unlimited_quota: true, remaining_quota_usd: 0,
  })
  editShow.value = true
}

async function submit() {
  if (!form.label) return message.warning('请填写标签')
  saving.value = true
  try {
    const payload = { ...form, allowed_models: form.allowed_models.length ? form.allowed_models : null }
    const { data } = await apiKeysApi.create(payload)
    createdToken.value = data.token
    createdShow.value = true
    editShow.value = false
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail?.error?.message || '创建失败')
  } finally { saving.value = false }
}

async function toggleStatus(row) {
  await apiKeysApi.update(row.id, { status: row.status === 'active' ? 'disabled' : 'active' })
  await load()
}

async function copyToken(id) {
  try {
    const { data } = await apiKeysApi.reveal(id)
    await navigator.clipboard.writeText(data.token)
    message.success(`已复制 ${data.preview} 到剪贴板`)
  } catch (e) {
    message.error(e?.response?.data?.detail?.error?.message || '复制失败')
  }
}

async function rotate(id) {
  const { data } = await apiKeysApi.rotate(id)
  createdToken.value = data.token
  createdShow.value = true
  await load()
}

async function remove(id) {
  await apiKeysApi.delete(id)
  await load()
}

function copyCreatedToken() {
  navigator.clipboard.writeText(createdToken.value)
  message.success('已复制')
}

onMounted(load)
</script>
