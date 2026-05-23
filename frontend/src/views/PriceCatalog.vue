<template>
  <n-space vertical size="large">
    <!-- 顶部工具栏 -->
    <n-card size="small" :bordered="false">
      <n-space justify="space-between" align="center">
        <n-space>
          <n-input
            v-model:value="filter.pattern"
            placeholder="按 pattern 模糊搜索"
            size="small"
            clearable
            style="width: 240px"
            @input="loadDebounced"
            @clear="load"
          />
          <n-select
            v-model:value="filter.source"
            :options="sourceOpts"
            placeholder="来源"
            size="small"
            clearable
            style="width: 140px"
            @update:value="load"
          />
        </n-space>
        <n-space>
          <n-button type="primary" @click="openAdd" size="small">新增价格</n-button>
          <n-button @click="openSyncModal" :loading="syncing" size="small">
            <template #icon><n-icon><CloudDownloadOutline /></n-icon></template>
            从 LiteLLM 同步
          </n-button>
          <n-button @click="load" size="small">刷新</n-button>
        </n-space>
      </n-space>
      <n-alert type="info" :show-icon="false" style="margin-top: 12px; font-size: 12px">
        Fireworks 官方 <code>/v1/models</code> 不返回价格，<code>fireworks.ai/pricing</code> 页面价格也被客户端 JS 动态加载（HTML 中拿不到）。
        解决方案：维护本地价格表 + 从 <b>LiteLLM 社区价格库</b> 同步（每周更新，含 265+ Fireworks 模型）。
        <br />
        <b>优先级</b>：模型 sync 时优先用 priority 高的条目，相同来源后创建优先；seed > manual > litellm 是默认建议。
      </n-alert>
    </n-card>

    <!-- 数据表 -->
    <n-data-table
      :columns="columns"
      :data="rows"
      :loading="loading"
      size="small"
      :scroll-x="1300"
      :pagination="{ pageSize: 30, showSizePicker: true, pageSizes: [20, 30, 50, 100] }"
      :bordered="false"
    />

    <!-- 新增/编辑 -->
    <n-modal v-model:show="editShow" preset="card" :title="editing.id ? '编辑价格条目' : '新增价格条目'" style="width: 560px">
      <n-form :model="editing" label-placement="top">
        <n-form-item label="模型匹配模式（pattern）" required>
          <n-input v-model:value="editing.pattern" placeholder="如 kimi-k2p6 或 deepseek-v4" />
        </n-form-item>
        <n-grid :cols="2" x-gap="12">
          <n-gi>
            <n-form-item label="匹配方式">
              <n-select
                v-model:value="editing.match_type"
                :options="[
                  { label: 'contains（模糊包含）', value: 'contains' },
                  { label: 'exact（完全相等）', value: 'exact' },
                  { label: 'prefix（前缀）', value: 'prefix' },
                ]"
              />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item label="单位">
              <n-select
                v-model:value="editing.unit"
                :options="[
                  { label: 'per_token (每 1M tokens)', value: 'per_token' },
                  { label: 'per_image (每张图)', value: 'per_image' },
                  { label: 'per_step (每 step)', value: 'per_step' },
                  { label: 'per_request (每请求)', value: 'per_request' },
                ]"
              />
            </n-form-item>
          </n-gi>
        </n-grid>
        <n-grid :cols="2" x-gap="12">
          <n-gi>
            <n-form-item label="输入价（$ / 1M tokens）">
              <n-input-number v-model:value="editing.input_per_1m" :precision="4" :min="0" :step="0.01" style="width: 100%" />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item label="输出价（$ / 1M tokens）">
              <n-input-number v-model:value="editing.output_per_1m" :precision="4" :min="0" :step="0.01" style="width: 100%" />
            </n-form-item>
          </n-gi>
        </n-grid>
        <n-form-item label="缓存输入价（$ / 1M tokens，可选）">
          <n-input-number v-model:value="editing.cached_input_per_1m" :precision="4" :min="0" :step="0.001" style="width: 100%" />
        </n-form-item>
        <n-grid :cols="2" x-gap="12">
          <n-gi>
            <n-form-item label="优先级">
              <n-input-number v-model:value="editing.priority" :min="0" :max="100" />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item label="启用">
              <n-switch v-model:value="editing.enabled" />
            </n-form-item>
          </n-gi>
        </n-grid>
        <n-form-item label="备注（可选）">
          <n-input v-model:value="editing.note" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="editShow = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="submit">{{ editing.id ? '保存' : '创建' }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 同步 LiteLLM 确认弹窗 -->
    <n-modal v-model:show="syncModalShow" preset="card" title="从 LiteLLM 同步价格" style="width: 480px">
      <n-alert type="warning" :show-icon="false" style="margin-bottom: 12px; font-size: 13px">
        将拉取 <b>github.com/BerriAI/litellm</b> 的 <code>model_prices_and_context_window.json</code>
        （含 265+ Fireworks 条目），并 upsert 到 <code>model_price_catalog</code> 表。
      </n-alert>
      <n-form>
        <n-form-item label="覆盖策略">
          <n-radio-group v-model:value="overwriteMode">
            <n-radio :value="false">
              <b>保守</b>：只更新 source=litellm 的旧条目；不动 seed / manual
            </n-radio>
            <br />
            <n-radio :value="true">
              <b>激进</b>：覆盖所有同 pattern 的条目（包括手动编辑过的）
            </n-radio>
          </n-radio-group>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="syncModalShow = false">取消</n-button>
          <n-button type="primary" :loading="syncing" @click="doSync">开始同步</n-button>
        </n-space>
      </template>
    </n-modal>
  </n-space>
</template>

<script setup>
import { ref, h, onMounted, reactive } from 'vue'
import { NTag, NButton, NPopconfirm, NSpace, NIcon, useMessage, useDialog } from 'naive-ui'
import { CloudDownloadOutline } from '@vicons/ionicons5'
import { priceCatalogApi } from '@/api'

const message = useMessage()
const dialog = useDialog()

const rows = ref([])
const loading = ref(false)
const syncing = ref(false)
const syncModalShow = ref(false)
const overwriteMode = ref(false)

const filter = reactive({ pattern: '', source: null })
const sourceOpts = [
  { label: 'seed (内置)', value: 'seed' },
  { label: 'manual (手动)', value: 'manual' },
  { label: 'litellm (社区库)', value: 'litellm' },
  { label: 'fireworks (爬虫)', value: 'fireworks' },
]

const editShow = ref(false)
const saving = ref(false)
const editing = reactive({
  id: null,
  pattern: '',
  match_type: 'contains',
  input_per_1m: 0,
  output_per_1m: 0,
  cached_input_per_1m: 0,
  per_image_usd: 0,
  per_step_usd: 0,
  unit: 'per_token',
  priority: 10,
  enabled: true,
  note: '',
})

const sourceColor = {
  seed:      { color: 'rgba(20,184,166,0.14)',  textColor: '#0d9488' },
  manual:    { color: 'rgba(245,158,11,0.14)',  textColor: '#d97706' },
  litellm:   { color: 'rgba(59,130,246,0.14)',  textColor: '#2563eb' },
  fireworks: { color: 'rgba(244,63,94,0.14)',   textColor: '#e11d48' },
}

const columns = [
  { title: 'ID', key: 'id', width: 60 },
  {
    title: '模式 (pattern)', key: 'pattern', width: 220, ellipsis: { tooltip: true },
    render: (r) => h('code', { style: 'font-size:12px;color:#0d9488' }, r.pattern),
  },
  {
    title: '匹配方式', key: 'match_type', width: 100,
    render: (r) => h(NTag, { size: 'small', bordered: false }, () => r.match_type),
  },
  {
    title: '来源', key: 'source', width: 90,
    render: (r) => h(NTag, { size: 'small', bordered: false, color: sourceColor[r.source] }, () => r.source),
  },
  {
    title: '输入 / 输出 ($/1M)', key: 'price', width: 180,
    render: (r) => h('span', { style: 'font-family:ui-monospace,Menlo,monospace;font-size:12px' },
      `$${r.input_per_1m.toFixed(3)} / $${r.output_per_1m.toFixed(3)}`),
  },
  {
    title: '缓存价', key: 'cached_input_per_1m', width: 100,
    render: (r) => r.cached_input_per_1m > 0
      ? h('span', { style: 'font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#9333ea' }, `$${r.cached_input_per_1m.toFixed(3)}`)
      : '-',
  },
  {
    title: '单位', key: 'unit', width: 110,
    render: (r) => h('span', { style: 'font-size:12px;color:var(--n-text-color-3,#94a3b8)' }, r.unit),
  },
  { title: '优先级', key: 'priority', width: 70 },
  {
    title: '启用', key: 'enabled', width: 70,
    render: (r) => r.enabled
      ? h(NTag, { size: 'tiny', bordered: false, type: 'success' }, () => '✓')
      : h(NTag, { size: 'tiny', bordered: false }, () => '禁'),
  },
  {
    title: '同步时间', key: 'last_synced_at', width: 140,
    render: (r) => r.last_synced_at
      ? h('span', { style: 'font-size:11px;color:var(--n-text-color-3,#94a3b8)' },
          new Date(r.last_synced_at).toLocaleString())
      : '-',
  },
  { title: '备注', key: 'note', ellipsis: { tooltip: true } },
  {
    title: '操作', key: 'actions', width: 130, fixed: 'right',
    render: (row) => h(NSpace, { size: 4 }, () => [
      h(NButton, { size: 'tiny', onClick: () => openEdit(row) }, () => '编辑'),
      h(NPopconfirm, { onPositiveClick: () => remove(row.id) }, {
        default: () => '删除此价格条目？',
        trigger: () => h(NButton, { size: 'tiny', type: 'error' }, () => '删除'),
      }),
    ]),
  },
]

let loadTimer = null
function loadDebounced() {
  if (loadTimer) clearTimeout(loadTimer)
  loadTimer = setTimeout(load, 400)
}

async function load() {
  loading.value = true
  try {
    const params = {}
    if (filter.pattern) params.pattern = filter.pattern
    if (filter.source) params.source = filter.source
    const { data } = await priceCatalogApi.list(params)
    rows.value = data
  } finally { loading.value = false }
}

function openAdd() {
  Object.assign(editing, {
    id: null, pattern: '', match_type: 'contains',
    input_per_1m: 0, output_per_1m: 0, cached_input_per_1m: 0,
    per_image_usd: 0, per_step_usd: 0, unit: 'per_token',
    priority: 10, enabled: true, note: '',
  })
  editShow.value = true
}

function openEdit(row) {
  Object.assign(editing, { ...row, note: row.note || '' })
  editShow.value = true
}

async function submit() {
  if (!editing.pattern) return message.warning('请填写 pattern')
  saving.value = true
  try {
    const payload = { ...editing }
    delete payload.id
    delete payload.source
    delete payload.created_at
    delete payload.updated_at
    delete payload.last_synced_at
    if (editing.id) {
      await priceCatalogApi.update(editing.id, payload)
      message.success('已更新')
    } else {
      await priceCatalogApi.create(payload)
      message.success('已创建')
    }
    editShow.value = false
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail || '操作失败')
  } finally { saving.value = false }
}

async function remove(id) {
  await priceCatalogApi.delete(id)
  message.success('已删除')
  await load()
}

function openSyncModal() {
  overwriteMode.value = false
  syncModalShow.value = true
}

async function doSync() {
  syncing.value = true
  try {
    const { data } = await priceCatalogApi.syncLitellm(overwriteMode.value)
    const msg = `共拉取 ${data.fetched_total} / Fireworks 命中 ${data.fireworks_matched} / 新建 ${data.created} / 更新 ${data.updated} / 跳过 ${data.skipped}`
    message.success(msg, { duration: 6000 })
    if (data.errors?.length) {
      dialog.error({
        title: '同步过程有错误',
        content: data.errors.join('\n'),
      })
    }
    syncModalShow.value = false
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail || '同步失败')
  } finally { syncing.value = false }
}

onMounted(load)
</script>
