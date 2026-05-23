<template>
  <n-space vertical>
    <n-space justify="space-between">
      <n-space>
        <n-button type="primary" @click="openAdd">新增模型</n-button>
        <n-button @click="sync" :loading="syncing">从 Fireworks 同步</n-button>
        <n-button @click="openImport">
          <template #icon><n-icon><DocumentAttachOutline /></n-icon></template>
          导入 JSON
        </n-button>
        <n-button @click="doExport">
          <template #icon><n-icon><DownloadOutline /></n-icon></template>
          导出 JSON
        </n-button>
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

    <!-- 导入 JSON 弹窗 -->
    <n-modal v-model:show="importShow" preset="card" title="导入模型 JSON" style="width: 720px">
      <n-tabs v-model:value="importTab" type="line">
        <n-tab-pane name="paste" tab="粘贴 JSON">
          <n-input
            v-model:value="importText"
            type="textarea"
            placeholder='[{"public_name":"gpt-oss-120b","fireworks_path":"accounts/fireworks/models/gpt-oss-120b","category":"chat","status":"active","input_price_per_1m":0.15,"output_price_per_1m":0.6}]'
            :rows="14"
            style="font-family: ui-monospace, Menlo, monospace"
          />
        </n-tab-pane>
        <n-tab-pane name="file" tab="上传文件">
          <n-upload :default-upload="false" accept=".json,application/json" :max="1" @change="onModelFileSelected">
            <n-upload-dragger>
              <div style="padding: 24px 0; text-align: center">
                <n-icon size="48" :depth="3"><DocumentAttachOutline /></n-icon>
                <div style="margin-top: 8px; font-size: 13px">点击或拖拽 .json 文件到此处</div>
              </div>
            </n-upload-dragger>
          </n-upload>
        </n-tab-pane>
        <n-tab-pane name="docs" tab="📖 格式说明">
          <n-text depth="3" style="font-size: 13px; display: block; margin-bottom: 8px">
            支持两种 JSON 形态。建议用「导出 JSON」拿当前列表当模板，编辑后再导入。
          </n-text>
          <n-divider style="margin: 12px 0 8px"><b>格式 1 — 原生数组（推荐）</b></n-divider>
          <n-code :code="modelExampleArray" language="json" :word-wrap="true" />

          <n-divider style="margin: 16px 0 8px"><b>格式 2 — 包装对象</b>（与 export-json 输出兼容）</n-divider>
          <n-code :code="modelExampleWrapped" language="json" :word-wrap="true" />

          <n-divider style="margin: 16px 0 8px"><b>字段说明</b></n-divider>
          <n-table :bordered="false" size="small" style="font-size: 12px">
            <thead>
              <tr><th>字段</th><th>类型</th><th>必填</th><th>说明</th></tr>
            </thead>
            <tbody>
              <tr><td><code>public_name</code></td><td>string</td><td>✓</td><td>对外暴露的模型名（如 <code>gpt-oss-120b</code>），客户端用它请求</td></tr>
              <tr><td><code>fireworks_path</code></td><td>string</td><td>—</td><td>Fireworks 真实路径（默认 <code>accounts/fireworks/models/{public_name}</code>）</td></tr>
              <tr><td><code>category</code></td><td>string</td><td>—</td><td><code>chat</code>(默认)/<code>completion</code>/<code>embedding</code>/<code>image</code>/<code>audio</code>/<code>rerank</code>/<code>vision</code></td></tr>
              <tr><td><code>status</code></td><td>string</td><td>—</td><td><code>active</code>(默认) / <code>disabled</code></td></tr>
              <tr><td><code>context_length</code></td><td>int</td><td>—</td><td>上下文长度</td></tr>
              <tr><td><code>input_price_per_1m</code></td><td>number</td><td>—</td><td>输入价 $ / 1M tokens</td></tr>
              <tr><td><code>output_price_per_1m</code></td><td>number</td><td>—</td><td>输出价 $ / 1M tokens</td></tr>
              <tr><td><code>cached_input_price_per_1m</code></td><td>number</td><td>—</td><td>缓存输入价</td></tr>
              <tr><td><code>supports_streaming / tools / vision / reasoning</code></td><td>bool</td><td>—</td><td>能力标记</td></tr>
              <tr><td><code>sort_order</code></td><td>int</td><td>—</td><td>列表排序</td></tr>
              <tr><td><code>description</code></td><td>string</td><td>—</td><td>备注</td></tr>
            </tbody>
          </n-table>
        </n-tab-pane>
      </n-tabs>

      <n-divider style="margin: 16px 0" />
      <n-form>
        <n-form-item label="合并策略" label-placement="left">
          <n-radio-group v-model:value="importStrategy">
            <n-space>
              <n-radio value="skip"><b>skip</b>（已存在 public_name → 跳过；最安全）</n-radio>
              <n-radio value="update"><b>update</b>（已存在 → 用新数据覆盖）</n-radio>
              <n-radio value="replace"><b>replace</b>（⚠️ 先清空 models 表再插入）</n-radio>
            </n-space>
          </n-radio-group>
        </n-form-item>
      </n-form>

      <template #footer>
        <n-space justify="end">
          <n-button @click="importShow = false">取消</n-button>
          <n-button type="primary" :loading="importing" @click="doImport">导入</n-button>
        </n-space>
      </template>
    </n-modal>

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
import { useRouter } from 'vue-router'
import { NTag, NButton, NIcon, NPopconfirm, NSpace, useMessage, useDialog } from 'naive-ui'
import { DocumentAttachOutline, DownloadOutline } from '@vicons/ionicons5'
import { modelApi } from '@/api'
import StatusDot from '@/components/StatusDot.vue'

const message = useMessage()
const dialog = useDialog()
const router = useRouter()

// 导入导出示例（在弹窗格式说明 tab 里展示）
const modelExampleArray = `[
  {
    "public_name": "gpt-oss-120b",
    "fireworks_path": "accounts/fireworks/models/gpt-oss-120b",
    "category": "chat",
    "status": "active",
    "context_length": 131072,
    "input_price_per_1m": 0.15,
    "output_price_per_1m": 0.60,
    "supports_streaming": true,
    "supports_tools": true,
    "supports_reasoning": true,
    "description": "GPT-OSS 120B"
  },
  {
    "public_name": "kimi-k2p6",
    "fireworks_path": "accounts/fireworks/models/kimi-k2p6",
    "category": "chat",
    "status": "active",
    "context_length": 262144,
    "input_price_per_1m": 0.55,
    "output_price_per_1m": 2.20,
    "supports_vision": true
  }
]`

const modelExampleWrapped = `{
  "count": 2,
  "items": [
    { "public_name": "gpt-oss-120b", "input_price_per_1m": 0.15, ... },
    { "public_name": "kimi-k2p6",    "input_price_per_1m": 0.55, ... }
  ]
}`
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
    const priced = data.priced ?? 0
    const unpriced = data.unpriced ?? 0
    message.success(
      `🎉 同步完成：新增 ${data.created} / 更新 ${data.updated} / 共 ${data.total}` +
      (priced ? ` · 自动填价 ${priced}` : '') +
      (unpriced ? ` · ${unpriced} 个待手动填价` : ''),
      { duration: 6000 },
    )
    await load()
  } catch (e) {
    // 后端返回的错误体可能有多种形态：
    //   1) {detail: {error: {message, type, details}}}  ← 新版结构化错误
    //   2) {error: {message, ...}}                       ← APIError 中间件
    //   3) {detail: "..."}                               ← 老版 HTTPException 字符串
    const detail = e?.response?.data?.detail
    const err = e?.response?.data?.error || detail?.error
    if (err?.type === 'no_upstream_keys') {
      dialog.warning({
        title: '需要先添加 Fireworks Key',
        content: () => h('div', [
          h('p', { style: 'margin: 0 0 12px 0' }, err.message),
          err.details?.hint && h('p', { style: 'margin: 0; color: var(--n-text-color-3, #64748b); font-size: 12px' }, err.details.hint),
        ]),
        positiveText: '去添加 Key',
        negativeText: '关闭',
        onPositiveClick: () => router.push('/upstream-keys'),
      })
    } else if (err?.type === 'no_active_upstream_keys') {
      dialog.warning({
        title: 'Key 池中无可用 Key',
        content: () => h('div', [
          h('p', { style: 'margin: 0 0 12px 0' }, err.message),
          err.details?.hint && h('p', { style: 'margin: 0; color: var(--n-text-color-3, #64748b); font-size: 12px' }, err.details.hint),
        ]),
        positiveText: '去 Key 池',
        negativeText: '关闭',
        onPositiveClick: () => router.push('/upstream-keys'),
      })
    } else if (err?.message) {
      message.error(err.message, { duration: 6000 })
    } else if (typeof detail === 'string') {
      message.error(detail)
    } else {
      message.error('同步失败：' + (e.message || '未知错误'))
    }
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

// ============= 导入 / 导出 JSON =============
const importShow = ref(false)
const importTab = ref('paste')
const importText = ref('')
const importStrategy = ref('skip')
const importing = ref(false)

function openImport() {
  importText.value = ''
  importTab.value = 'paste'
  importStrategy.value = 'skip'
  importShow.value = true
}

async function onModelFileSelected({ file }) {
  if (!file || !file.file) return
  try {
    const text = await file.file.text()
    importText.value = text
    importTab.value = 'paste'
    message.success(`已加载文件 ${file.name}（${(text.length / 1024).toFixed(1)} KB）`)
  } catch (e) {
    message.error('读取文件失败：' + e.message)
  }
}

async function doImport() {
  if (!importText.value.trim()) {
    message.warning('请先粘贴或上传 JSON')
    return
  }
  let data
  try { data = JSON.parse(importText.value) }
  catch (e) { return message.error('JSON 解析失败：' + e.message) }

  if (importStrategy.value === 'replace') {
    const ok = await new Promise((resolve) => {
      dialog.warning({
        title: '⚠️ 危险操作：replace 模式',
        content: '会先清空整个 models 表，然后插入新数据。原有手填的价格 / 启用状态都会丢失。确认吗？',
        positiveText: '我了解风险，继续',
        negativeText: '取消',
        onPositiveClick: () => resolve(true),
        onNegativeClick: () => resolve(false),
      })
    })
    if (!ok) return
  }

  importing.value = true
  try {
    const { data: result } = await modelApi.importJson(data, importStrategy.value)
    const summary = `收到 ${result.received} / 新建 ${result.created} / 更新 ${result.updated} / 跳过 ${result.skipped}`
    message.success(summary, { duration: 6000 })
    if (result.errors?.length) {
      dialog.warning({
        title: `导入有 ${result.errors.length} 条错误`,
        content: result.errors.slice(0, 10).join('\n'),
      })
    }
    importShow.value = false
    await load()
  } catch (e) {
    const detail = e?.response?.data?.detail
    const errMsg = (typeof detail === 'string' ? detail : detail?.error?.message) || e?.message || '导入失败'
    message.error(errMsg)
  } finally { importing.value = false }
}

async function doExport() {
  try {
    const { data } = await modelApi.exportJson()
    const json = JSON.stringify(data.items, null, 2)
    const blob = new Blob([json], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `fireworkrouter-models-${new Date().toISOString().slice(0, 10)}.json`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    message.success(`已下载 ${data.count} 个模型`)
  } catch (e) {
    message.error('导出失败：' + (e.message || ''))
  }
}

onMounted(load)
</script>
