<template>
  <div class="login-wrap fwr-mesh-bg" :class="{ dark: themeStore.isDark }">
    <!-- 装饰光球 -->
    <div class="fwr-blur-orb" style="width: 380px; height: 380px; top: -120px; right: -100px;
         background: radial-gradient(circle, rgba(20, 184, 166, 0.32) 0%, transparent 70%);" />
    <div class="fwr-blur-orb" style="width: 320px; height: 320px; bottom: -80px; left: -80px;
         background: radial-gradient(circle, rgba(45, 212, 191, 0.28) 0%, transparent 70%);" />
    <div class="fwr-blur-orb" style="width: 460px; height: 460px; top: 30%; left: 40%;
         background: radial-gradient(circle, rgba(13, 148, 136, 0.18) 0%, transparent 70%);" />

    <!-- 网格纹理 overlay -->
    <div class="grid-overlay" :class="{ dark: themeStore.isDark }"></div>

    <!-- 内容 -->
    <div class="login-content">
      <!-- 品牌区 -->
      <div class="brand-block">
        <div class="brand-logo fwr-logo-glow">🎆</div>
        <h1 class="brand-title fwr-text-gradient">FireworkRouter</h1>
        <p class="brand-sub">Fireworks.ai 多 Key 智能调度管理控制台</p>
      </div>

      <!-- 登录卡 -->
      <n-card class="login-card" :bordered="false">
        <n-form ref="formRef" :model="form" :rules="rules" label-placement="top">
          <n-form-item label="管理密码" path="password">
            <n-input
              v-model:value="form.password"
              type="password"
              show-password-on="click"
              placeholder="输入管理密码"
              size="large"
              @keyup.enter="submit"
            />
          </n-form-item>
        </n-form>
        <n-button type="primary" block size="large" :loading="loading" @click="submit">
          登录
        </n-button>
        <div class="login-hint">
          初始密码：<code>admin</code>，请尽快修改
        </div>
      </n-card>

      <!-- 主题切换 -->
      <div class="theme-switch">
        <n-button text @click="themeStore.toggle()">
          <template #icon>
            <n-icon size="18">
              <component :is="themeStore.isDark ? SunnyOutline : MoonOutline" />
            </n-icon>
          </template>
          {{ themeStore.isDark ? '切换浅色' : '切换深色' }}
        </n-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useMessage } from 'naive-ui'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { MoonOutline, SunnyOutline } from '@vicons/ionicons5'

const router = useRouter()
const message = useMessage()
const auth = useAuthStore()
const themeStore = useThemeStore()

const loading = ref(false)
const formRef = ref(null)
const form = reactive({ password: '' })

const rules = {
  password: { required: true, message: '请输入密码', trigger: 'blur' },
}

async function submit() {
  try { await formRef.value?.validate() } catch (_) { return }
  loading.value = true
  try {
    await auth.login(form.password)
    message.success('登录成功')
    router.push('/dashboard')
  } catch (e) {
    const detail = e?.response?.data?.detail
    let msg
    if (detail?.error?.message) msg = detail.error.message
    else if (typeof detail === 'string') msg = detail
    else msg = e?.message || '登录失败'
    message.error(msg)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
}

.grid-overlay {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(20, 184, 166, 0.06) 1px, transparent 1px),
    linear-gradient(90deg, rgba(20, 184, 166, 0.06) 1px, transparent 1px);
  background-size: 56px 56px;
  pointer-events: none;
}
.grid-overlay.dark {
  background-image:
    linear-gradient(rgba(20, 184, 166, 0.08) 1px, transparent 1px),
    linear-gradient(90deg, rgba(20, 184, 166, 0.08) 1px, transparent 1px);
}

.login-content {
  position: relative;
  z-index: 10;
  width: 100%;
  max-width: 420px;
  padding: 0 24px;
}

.brand-block {
  text-align: center;
  margin-bottom: 32px;
}
.brand-logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 72px;
  height: 72px;
  border-radius: 20px;
  background: linear-gradient(135deg, #14b8a6, #0d9488);
  font-size: 36px;
  margin-bottom: 16px;
}
.brand-title {
  font-size: 30px;
  margin: 0 0 6px 0;
  letter-spacing: -0.5px;
}
.brand-sub {
  font-size: 14px;
  color: #64748b;
  margin: 0;
}

.login-card {
  border-radius: 20px;
  box-shadow:
    0 20px 50px rgba(15, 23, 42, 0.08),
    0 4px 12px rgba(20, 184, 166, 0.06);
  backdrop-filter: blur(20px);
  background-color: rgba(255, 255, 255, 0.85);
}

:deep(.dark) .login-card {
  background-color: rgba(15, 23, 42, 0.75);
}

.login-hint {
  margin-top: 14px;
  font-size: 12px;
  color: #94a3b8;
  text-align: center;
}
.login-hint code {
  background: rgba(20, 184, 166, 0.12);
  color: #0d9488;
  padding: 2px 6px;
  border-radius: 4px;
  font-family: ui-monospace, Menlo, monospace;
}

.theme-switch {
  margin-top: 20px;
  text-align: center;
}
</style>
