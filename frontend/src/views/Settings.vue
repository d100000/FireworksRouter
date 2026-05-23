<template>
  <n-space vertical size="large">
    <n-card title="调度策略">
      <n-form label-placement="left" label-width="200">
        <n-form-item label="调度算法">
          <n-select v-model:value="form['scheduler.strategy']" :options="strategyOpts" style="width: 280px" />
        </n-form-item>
        <n-form-item label="Session 粘性字段" v-if="form['scheduler.strategy'] === 'session_sticky'">
          <n-select
            v-model:value="form['scheduler.session_sticky_field']"
            :options="[
              { label: 'prompt_cache_key', value: 'prompt_cache_key' },
              { label: 'user', value: 'user' },
            ]"
            style="width: 280px"
          />
        </n-form-item>
        <n-alert type="info" :show-icon="false">
          <strong>策略说明：</strong>
          <ul style="margin: 6px 0 0 20px; padding: 0">
            <li><b>weighted_random</b>：取最高 priority 子集后按 weight 加权随机（默认）</li>
            <li><b>round_robin</b>：按 ID 严格轮询（per-model 游标）</li>
            <li><b>priority</b>：严格 priority 降序，同级再加权随机</li>
            <li><b>least_used</b>：选 last_used_at 最早的</li>
            <li><b>most_balance</b>：选余额最高的</li>
            <li><b>session_sticky</b>：按 prompt_cache_key / user 等 8 源 fallback 一致性哈希</li>
            <li><b>fill_first</b> ⭐：永远取候选池第一把（priority+ID 最小），冷却才让位 — 适合按窗口结算的订阅 Key</li>
          </ul>
        </n-alert>
      </n-form>
    </n-card>

    <n-card title="网关重试">
      <n-form label-placement="left" label-width="200">
        <n-form-item label="最多尝试 Key 数（max_retry_credentials）">
          <n-input-number v-model:value="form['gateway.max_retry_credentials']" :min="1" :max="20" style="width: 280px" />
        </n-form-item>
        <n-form-item label="冷却恢复最长等待（秒）">
          <n-input-number v-model:value="form['gateway.max_retry_interval_s']" :min="0" :max="300" style="width: 280px" />
        </n-form-item>
        <n-form-item label="全局重试次数">
          <n-input-number v-model:value="form['gateway.max_retry']" :min="0" :max="10" style="width: 280px" />
        </n-form-item>
      </n-form>
    </n-card>

    <n-card title="错误码冷却">
      <n-form label-placement="left" label-width="200">
        <n-grid :cols="2" x-gap="20">
          <n-gi>
            <n-form-item label="401/403 凭据失效（秒）">
              <n-input-number v-model:value="form['cooldown.401_seconds']" :min="0" style="width: 100%" />
            </n-form-item>
            <n-form-item label="402 余额不足（秒）">
              <n-input-number v-model:value="form['cooldown.402_seconds']" :min="0" style="width: 100%" />
            </n-form-item>
            <n-form-item label="404 模型不支持（秒，per-Key-Model）">
              <n-input-number v-model:value="form['cooldown.404_seconds']" :min="0" style="width: 100%" />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item label="429 起始（秒，指数退避）">
              <n-input-number v-model:value="form['cooldown.429_initial_seconds']" :min="0" style="width: 100%" />
            </n-form-item>
            <n-form-item label="429 上限（秒）">
              <n-input-number v-model:value="form['cooldown.429_max_seconds']" :min="0" style="width: 100%" />
            </n-form-item>
            <n-form-item label="5xx 起始 / 上限（秒）">
              <n-space>
                <n-input-number v-model:value="form['cooldown.5xx_initial_seconds']" :min="0" />
                <n-input-number v-model:value="form['cooldown.5xx_max_seconds']" :min="0" />
              </n-space>
            </n-form-item>
          </n-gi>
        </n-grid>
        <n-alert type="warning" :show-icon="false">
          冷却时长改动后立即生效（不重启）。401/402 长冷却同时会置 auto_disabled。
        </n-alert>
      </n-form>
    </n-card>

    <n-card title="探针">
      <n-form label-placement="left" label-width="200">
        <n-form-item label="最低余额阈值（USD）">
          <n-input-number v-model:value="form['probe.min_balance_usd']" :precision="2" :min="0" :step="0.1" style="width: 280px" />
        </n-form-item>
        <n-form-item label="探测间隔（分钟）">
          <n-input-number v-model:value="form['probe.interval_minutes']" :min="1" style="width: 280px" />
          <n-text depth="3" style="margin-left: 8px">改后需重启生效</n-text>
        </n-form-item>
      </n-form>
    </n-card>

    <!-- ============ 日志保留 & 清理 ============ -->
    <n-card title="日志保留 & 清理">
      <n-form label-placement="left" label-width="220">
        <n-grid :cols="2" x-gap="20">
          <n-gi>
            <n-form-item label="应用日志最低级别">
              <n-select
                v-model:value="form['system_log_min_level']"
                :options="logLevelOpts"
                style="width: 100%"
              />
            </n-form-item>
            <n-form-item label="应用日志保留（天）">
              <n-input-number v-model:value="form['system_logs_retention_days']" :min="1" :max="365" style="width: 100%" />
            </n-form-item>
            <n-form-item label="调用日志保留（天）">
              <n-input-number v-model:value="form['logs_retention_days']" :min="1" :max="365" style="width: 100%" />
            </n-form-item>
          </n-gi>
          <n-gi>
            <n-form-item label="探针历史保留（天）">
              <n-input-number v-model:value="form['probe_history_retention_days']" :min="1" :max="90" style="width: 100%" />
            </n-form-item>
            <n-form-item label="监控桶保留（小时）">
              <n-input-number v-model:value="form['metric_buckets_retention_hours']" :min="1" :max="168" style="width: 100%" />
            </n-form-item>
          </n-gi>
        </n-grid>
        <n-alert type="info" :show-icon="false" style="margin-bottom: 12px">
          应用日志（loguru）默认只把 <b>WARNING+</b> 写入 DB；INFO/DEBUG 只去 stdout 不入库。
          后台每小时自动清理一次；下面的「立即清理」按钮立即执行一次。
        </n-alert>

        <!-- 表大小预览 -->
        <n-descriptions
          v-if="cleanupStatus"
          label-placement="top"
          :column="4"
          size="small"
          bordered
          style="margin-bottom: 12px"
        >
          <n-descriptions-item label="应用日志">
            <span class="mono">{{ cleanupStatus.tables.system_logs.rows }} 条</span>
            <div class="muted small">{{ rangeOf('system_logs') }}</div>
          </n-descriptions-item>
          <n-descriptions-item label="调用日志">
            <span class="mono">{{ cleanupStatus.tables.request_logs.rows }} 条</span>
            <div class="muted small">{{ rangeOf('request_logs') }}</div>
          </n-descriptions-item>
          <n-descriptions-item label="探针历史">
            <span class="mono">{{ cleanupStatus.tables.probe_history.rows }} 条</span>
            <div class="muted small">{{ rangeOf('probe_history') }}</div>
          </n-descriptions-item>
          <n-descriptions-item label="监控桶">
            <span class="mono">{{ cleanupStatus.tables.key_metric_buckets.rows }} 条</span>
            <div class="muted small">{{ rangeOf('key_metric_buckets') }}</div>
          </n-descriptions-item>
        </n-descriptions>

        <n-space>
          <n-button @click="loadCleanupStatus" :loading="statusLoading">刷新表大小</n-button>
          <n-popconfirm @positive-click="runCleanupNow" :show-icon="false">
            <template #trigger>
              <n-button type="warning" :loading="cleanupLoading">立即清理过期日志</n-button>
            </template>
            按当前保留期立即跑一次清理（删除超期数据）？
          </n-popconfirm>
          <span v-if="lastDeleted" class="muted small" style="align-self: center">
            上次清理：app={{ lastDeleted.system_logs }} / req={{ lastDeleted.request_logs }} /
            probe={{ lastDeleted.probe_history }} / 桶={{ lastDeleted.metric_buckets }}
          </span>
        </n-space>
      </n-form>
    </n-card>

    <n-space justify="end">
      <n-button @click="load">重置</n-button>
      <n-button type="primary" :loading="saving" @click="save">保存所有变更</n-button>
    </n-space>
  </n-space>
</template>

<script setup>
import { ref, onMounted, computed, reactive } from 'vue'
import { useMessage, NPopconfirm, NDescriptions, NDescriptionsItem } from 'naive-ui'
import { settingsApi, logsApi } from '@/api'

const message = useMessage()

const strategies = ref([])
const form = reactive({
  'scheduler.strategy': 'weighted_random',
  'scheduler.session_sticky_field': 'prompt_cache_key',
  'gateway.max_retry': 3,
  'gateway.max_retry_credentials': 3,
  'gateway.max_retry_interval_s': 30,
  'probe.min_balance_usd': 0.5,
  'probe.interval_minutes': 15,
  'cooldown.401_seconds': 1800,
  'cooldown.402_seconds': 3600,
  'cooldown.404_seconds': 43200,
  'cooldown.429_initial_seconds': 1,
  'cooldown.429_max_seconds': 1800,
  'cooldown.5xx_initial_seconds': 60,
  'cooldown.5xx_max_seconds': 1800,
  // 日志保留
  'logs_retention_days': 30,
  'system_logs_retention_days': 14,
  'probe_history_retention_days': 7,
  'metric_buckets_retention_hours': 25,
  'system_log_min_level': 'WARNING',
})
const saving = ref(false)
const cleanupStatus = ref(null)
const statusLoading = ref(false)
const cleanupLoading = ref(false)
const lastDeleted = ref(null)

const logLevelOpts = [
  { label: 'DEBUG（最详细，量大）', value: 'DEBUG' },
  { label: 'INFO（一般信息）', value: 'INFO' },
  { label: 'WARNING（推荐，警告及以上）', value: 'WARNING' },
  { label: 'ERROR（仅错误）', value: 'ERROR' },
  { label: 'CRITICAL（仅严重错误）', value: 'CRITICAL' },
]

const strategyOpts = computed(() => strategies.value.map(v => ({ label: v, value: v })))

async function load() {
  const { data } = await settingsApi.get()
  strategies.value = data.schedule_strategies
  for (const k of Object.keys(form)) {
    if (data.items[k] !== undefined) form[k] = data.items[k]
  }
  await loadCleanupStatus()
}

async function loadCleanupStatus() {
  statusLoading.value = true
  try {
    const { data } = await logsApi.cleanupStatus()
    cleanupStatus.value = data
  } catch (e) {
    message.error(`加载表大小失败：${e?.message || e}`)
  } finally {
    statusLoading.value = false
  }
}

async function runCleanupNow() {
  cleanupLoading.value = true
  try {
    const { data } = await logsApi.cleanupRunNow()
    lastDeleted.value = data.deleted
    message.success(
      `清理完成 — app:${data.deleted.system_logs}/req:${data.deleted.request_logs}/probe:${data.deleted.probe_history}/桶:${data.deleted.metric_buckets}`,
    )
    await loadCleanupStatus()
  } catch (e) {
    message.error(`清理失败：${e?.message || e}`)
  } finally {
    cleanupLoading.value = false
  }
}

function rangeOf(table) {
  const t = cleanupStatus.value?.tables?.[table]
  if (!t || !t.oldest) return '—'
  const fmt = (s) => new Date(s).toLocaleDateString('zh-CN')
  return `${fmt(t.oldest)} ~ ${fmt(t.newest)}`
}

async function save() {
  saving.value = true
  try {
    await settingsApi.patch({ ...form })
    message.success('已保存')
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail || '保存失败')
  } finally { saving.value = false }
}

onMounted(load)
</script>

<style scoped>
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-weight: 600;
}
.muted {
  color: var(--n-text-color-3, #888);
}
.small {
  font-size: 12px;
}
</style>
