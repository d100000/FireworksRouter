<template>
  <n-space vertical>
    <n-space justify="space-between">
      <n-space>
        <n-button type="primary" @click="openAdd">新增模型</n-button>
        <n-button @click="sync" :loading="syncing">从 Fireworks 同步</n-button>
        <n-button @click="load">刷新</n-button>
        <n-button :disabled="!checkedRowKeys.length" @click="batchEnable">批量启用 ({{ checkedRowKeys.length }})</n-button>
        <n-button :disabled="!checkedRowKeys.length" @click="batchDisable">批量禁用</n-button>
      </n-space>
      <n-text depth="3">共 {{ rows.length }} 个</n-text>
    </n-space>

    <n-data-table
      :columns="columns" :data="rows" :loading="loading" size="small" :scroll-x="1600"
      :row-key="(row) => row.id"
      v-model:checked-row-keys="checkedRowKeys"
    />

    <n-modal v-model:show="editShow" preset="card" :title="editing.id ? '编辑模型' : '新增模型'" style="width: 720px">
      <n-form :model="editing" label-placement="top">
        <n-grid :cols="2" x-gap="12">
          <n-gi>
            <n-form-item label="对外名称（public_name）" required>
              <n-input v-model:value="editing.public_name" />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item label="Fireworks 路径" required>
              <n-input v-model:value="editing.fireworks_path" placeholder="accounts/fireworks/models/..." />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item label="分类">
              <n-select v-model:value="editing.category" :options="categoryOpts" />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item label="状态">
              <n-select v-model:value="editing.status" :options="[
                { label: 'active', value: 'active' }, { label: 'disabled', value: 'disabled' }
              ]" />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item label="Context Length">
              <n-input-number v-model:value="editing.context_length" :min="0" />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item label="Max Output Tokens">
              <n-input-number v-model:value="editing.max_output_tokens" :min="0" />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item label="输入价 (USD / 1M)">
              <n-input-number v-model:value="editing.input_price_per_1m" :precision="4" :step="0.01" />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item label="输出价 (USD / 1M)">
              <n-input-number v-model:value="editing.output_price_per_1m" :precision="4" :step="0.01" />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item label="缓存输入价 (USD / 1M)">
              <n-input-number v-model:value="editing.cached_input_price_per_1m" :precision="4" :step="0.01" />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item label="排序">
              <n-input-number v-model:value="editing.sort_order" />
            </n-form-item>
          </n-gi>
        </n-grid>
        <n-space>
          <n-checkbox v-model:checked="editing.supports_streaming">流式</n-checkbox>
          <n-checkbox v-model:checked="editing.supports_tools">工具调用</n-checkbox>
          <n-checkbox v-model:checked="editing.supports_vision">视觉</n-checkbox>
          <n-checkbox v-model:checked="editing.supports_reasoning">思考链</n-checkbox>
        </n-space>
        <n-form-item label="描述">
          <n-input v-model:value="editing.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="editShow = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="submit">保存</n-button>
        </n-space>
      </template>
    </n-modal>
  </n-space>
</template>

<script setup>
import { ref, h, onMounted, reactive } from 'vue'
import { NTag, NButton, NPopconfirm, NSpace, useMessage } from 'naive-ui'
import { modelApi } from '@/api'
import StatusDot from '@/components/StatusDot.vue'

const message = useMessage()
const rows = ref([])
const loading = ref(false)
const syncing = ref(false)
const editShow = ref(false)
const saving = ref(false)
const editing = reactive({})

const categoryOpts = [
  'chat', 'completion', 'embedding', 'image', 'audio', 'rerank', 'vision', 'other'
].map((v) => ({ label: v, value: v }))

const checkedRowKeys = ref([])

const columns = [
  { type: 'selection', width: 40 },
  { title: 'ID', key: 'id', width: 60 },
  { title: 'public_name', key: 'public_name', width: 200 },
  { title: 'fireworks_path', key: 'fireworks_path', width: 320, ellipsis: { tooltip: true } },
  {
    title: '分类', key: 'category', width: 100,
    render: (r) => h(NTag, { size: 'small' }, () => r.category),
  },
  {
    title: '状态', key: 'status', width: 100,
    render: (r) => h(StatusDot, { status: r.status, label: r.status }),
  },
  { title: 'CtxLen', key: 'context_length', width: 90 },
  {
    title: '输入价', key: 'input_price_per_1m', width: 100,
    render: (r) => `$${r.input_price_per_1m}/1M`,
  },
  {
    title: '输出价', key: 'output_price_per_1m', width: 100,
    render: (r) => `$${r.output_price_per_1m}/1M`,
  },
  {
    title: '能力', key: 'caps', width: 200,
    render: (r) => h(NSpace, { size: 4 }, () => [
      r.supports_streaming && h(NTag, { size: 'tiny' }, () => 'stream'),
      r.supports_tools && h(NTag, { size: 'tiny', type: 'info' }, () => 'tools'),
      r.supports_vision && h(NTag, { size: 'tiny', type: 'success' }, () => 'vision'),
      r.supports_reasoning && h(NTag, { size: 'tiny', type: 'warning' }, () => 'think'),
    ].filter(Boolean)),
  },
  {
    title: '操作', key: 'actions', fixed: 'right', width: 180,
    render: (row) => h(NSpace, { size: 'small' }, () => [
      h(NButton, { size: 'tiny', onClick: () => openEdit(row) }, () => '编辑'),
      h(NButton, {
        size: 'tiny',
        type: row.status === 'active' ? 'warning' : 'primary',
        onClick: () => toggleStatus(row),
      }, () => row.status === 'active' ? '禁用' : '启用'),
      h(NPopconfirm, { onPositiveClick: () => removeRow(row.id) }, {
        default: () => '确认删除？',
        trigger: () => h(NButton, { size: 'tiny', type: 'error' }, () => '删除'),
      }),
    ]),
  },
]

async function load() {
  loading.value = true
  try {
    const { data } = await modelApi.list()
    rows.value = data
  } finally {
    loading.value = false
  }
}

async function sync() {
  syncing.value = true
  try {
    const { data } = await modelApi.sync()
    message.success(`同步完成：新增 ${data.created} / 更新 ${data.updated} / 共 ${data.total}`)
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail || '同步失败')
  } finally {
    syncing.value = false
  }
}

function openAdd() {
  Object.assign(editing, {
    id: null, public_name: '', fireworks_path: '', category: 'chat', status: 'active',
    context_length: 0, max_output_tokens: 0, input_price_per_1m: 0, output_price_per_1m: 0,
    cached_input_price_per_1m: 0, sort_order: 0,
    supports_streaming: true, supports_tools: false, supports_vision: false, supports_reasoning: false,
    description: '',
  })
  editShow.value = true
}

function openEdit(row) {
  Object.assign(editing, { ...row })
  editShow.value = true
}

async function submit() {
  saving.value = true
  try {
    if (editing.id) {
      await modelApi.update(editing.id, editing)
    } else {
      await modelApi.create(editing)
    }
    message.success('已保存')
    editShow.value = false
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function toggleStatus(row) {
  await modelApi.update(row.id, { status: row.status === 'active' ? 'disabled' : 'active' })
  await load()
}

async function removeRow(id) {
  await modelApi.delete(id)
  await load()
}

async function batchEnable() {
  const { data } = await modelApi.batchStatus(checkedRowKeys.value, 'active')
  message.success(`已启用 ${data.updated} / ${data.requested}`)
  checkedRowKeys.value = []
  await load()
}

async function batchDisable() {
  const { data } = await modelApi.batchStatus(checkedRowKeys.value, 'disabled')
  message.success(`已禁用 ${data.updated} / ${data.requested}`)
  checkedRowKeys.value = []
  await load()
}

onMounted(load)
</script>
