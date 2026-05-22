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

    <n-space justify="end">
      <n-button @click="load">重置</n-button>
      <n-button type="primary" :loading="saving" @click="save">保存所有变更</n-button>
    </n-space>
  </n-space>
</template>

<script setup>
import { ref, onMounted, computed, reactive } from 'vue'
import { useMessage } from 'naive-ui'
import { settingsApi } from '@/api'

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
})
const saving = ref(false)

const strategyOpts = computed(() => strategies.value.map(v => ({ label: v, value: v })))

async function load() {
  const { data } = await settingsApi.get()
  strategies.value = data.schedule_strategies
  for (const k of Object.keys(form)) {
    if (data.items[k] !== undefined) form[k] = data.items[k]
  }
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
