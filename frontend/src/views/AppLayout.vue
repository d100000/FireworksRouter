<template>
  <n-layout has-sider style="height: 100vh">
    <n-layout-sider
      bordered
      collapse-mode="width"
      :collapsed-width="72"
      :width="240"
      show-trigger="arrow-circle"
      :native-scrollbar="false"
      :collapsed="collapsed"
      @update:collapsed="(v) => collapsed = v"
    >
      <!-- 品牌区 -->
      <div class="brand" :class="{ collapsed }">
        <div class="brand-logo fwr-logo-glow">🎆</div>
        <span v-if="!collapsed" class="brand-text fwr-text-gradient">FireworkRouter</span>
      </div>

      <n-menu
        :options="menuOptions"
        :value="currentRoute"
        :collapsed="collapsed"
        :collapsed-width="72"
        :collapsed-icon-size="20"
        @update:value="onSelect"
        :root-indent="18"
      />
    </n-layout-sider>

    <n-layout>
      <!-- 顶部 Glass Header -->
      <n-layout-header
        class="fwr-glass"
        :class="{ dark: themeStore.isDark }"
        :bordered="false"
        :style="{ position: 'sticky', top: 0, zIndex: 30, padding: '0 24px', height: '64px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }"
      >
        <div style="font-weight: 600; font-size: 16px">{{ pageTitle }}</div>
        <n-space align="center" :size="14">
          <!-- 主题切换 -->
          <n-button quaternary circle @click="themeStore.toggle()" :title="themeStore.isDark ? '切换浅色' : '切换深色'">
            <template #icon>
              <n-icon size="18">
                <component :is="themeStore.isDark ? SunnyOutline : MoonOutline" />
              </n-icon>
            </template>
          </n-button>

          <!-- API 文档链接 -->
          <n-button quaternary circle title="API 文档" @click="openDocs">
            <template #icon><n-icon size="18"><DocumentTextOutline /></n-icon></template>
          </n-button>

          <n-divider vertical />

          <!-- 用户头像下拉 -->
          <n-dropdown trigger="hover" :options="userMenu" @select="onUserMenu">
            <div class="user-chip">
              <div class="user-avatar">A</div>
              <span class="user-label">Admin</span>
            </div>
          </n-dropdown>

          <!-- 修改密码弹窗 -->
          <n-modal v-model:show="pwShow" preset="card" title="修改管理密码" style="width: 440px">
            <n-form ref="pwFormRef" :model="pwForm" :rules="pwRules" label-placement="top">
              <n-form-item label="当前密码" path="old_password">
                <n-input v-model:value="pwForm.old_password" type="password" show-password-on="click" placeholder="旧密码" />
              </n-form-item>
              <n-form-item label="新密码（≥ 8 位）" path="new_password">
                <n-input v-model:value="pwForm.new_password" type="password" show-password-on="click" placeholder="新密码" />
              </n-form-item>
              <n-form-item label="确认新密码" path="confirm">
                <n-input v-model:value="pwForm.confirm" type="password" show-password-on="click" placeholder="再次输入" @keyup.enter="submitPw" />
              </n-form-item>
            </n-form>
            <n-alert type="info" :show-icon="false" style="margin-bottom: 12px">
              修改后立即生效，下次登录使用新密码。当前 session 仍有效至过期。
            </n-alert>
            <template #footer>
              <n-space justify="end">
                <n-button @click="pwShow = false">取消</n-button>
                <n-button type="primary" :loading="pwSaving" @click="submitPw">保存</n-button>
              </n-space>
            </template>
          </n-modal>
        </n-space>
      </n-layout-header>

      <n-layout-content content-style="padding: 24px; height: calc(100vh - 64px); overflow: auto">
        <router-view />
      </n-layout-content>
    </n-layout>
  </n-layout>
</template>

<script setup>
import { computed, h, ref, reactive } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NIcon, useMessage } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { authApi } from '@/api'
import {
  StatsChartOutline, KeyOutline, CubeOutline,
  DocumentTextOutline, PulseOutline, LockClosedOutline,
  LogOutOutline, SettingsOutline, MoonOutline, SunnyOutline,
  GitNetworkOutline, ShieldCheckmarkOutline,
} from '@vicons/ionicons5'

const message = useMessage()

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const themeStore = useThemeStore()
const collapsed = ref(false)

const renderIcon = (icon) => () => h(NIcon, null, { default: () => h(icon) })

const menuOptions = [
  { label: '概览', key: '/dashboard', icon: renderIcon(StatsChartOutline) },
  { label: '上游 Key 池', key: '/upstream-keys', icon: renderIcon(KeyOutline) },
  { label: '模型管理', key: '/models', icon: renderIcon(CubeOutline) },
  { label: 'API Keys', key: '/api-keys', icon: renderIcon(LockClosedOutline) },
  { label: '调度轨迹', key: '/request-trace', icon: renderIcon(GitNetworkOutline) },
  { label: '调用日志', key: '/request-logs', icon: renderIcon(DocumentTextOutline) },
  { label: '探针历史', key: '/probe-logs', icon: renderIcon(PulseOutline) },
  { label: '系统设置', key: '/settings', icon: renderIcon(SettingsOutline) },
]

const currentRoute = computed(() => route.path)
const pageTitle = computed(() => route.meta?.title || '')

function onSelect(key) { router.push(key) }

const userMenu = [
  { label: '修改密码', key: 'change-password', icon: renderIcon(ShieldCheckmarkOutline) },
  { type: 'divider' },
  { label: '退出登录', key: 'logout', icon: renderIcon(LogOutOutline) },
]

const pwShow = ref(false)
const pwSaving = ref(false)
const pwFormRef = ref(null)
const pwForm = reactive({ old_password: '', new_password: '', confirm: '' })
const pwRules = {
  old_password: { required: true, message: '请输入当前密码', trigger: 'blur' },
  new_password: { required: true, min: 8, message: '新密码至少 8 位', trigger: 'blur' },
  confirm: {
    validator(_, value) {
      if (!value) return new Error('请确认新密码')
      if (value !== pwForm.new_password) return new Error('两次输入不一致')
      return true
    },
    trigger: 'blur',
  },
}

async function submitPw() {
  try { await pwFormRef.value?.validate() } catch (_) { return }
  pwSaving.value = true
  try {
    await authApi.changePassword({
      old_password: pwForm.old_password,
      new_password: pwForm.new_password,
    })
    message.success('密码已更新，下次登录使用新密码')
    pwShow.value = false
    pwForm.old_password = ''
    pwForm.new_password = ''
    pwForm.confirm = ''
  } catch (e) {
    message.error(e?.response?.data?.detail?.error?.message || '修改失败')
  } finally {
    pwSaving.value = false
  }
}

function onUserMenu(key) {
  if (key === 'logout') {
    auth.logout()
    router.push('/login')
  } else if (key === 'change-password') {
    pwShow.value = true
  }
}

function openDocs() { window.open('/docs', '_blank') }
</script>

<style scoped>
.brand {
  height: 64px;
  display: flex;
  align-items: center;
  padding: 0 18px;
  gap: 12px;
  font-weight: 700;
  font-size: 16px;
  border-bottom: 1px solid var(--n-border-color);
  transition: all 0.2s;
}
.brand.collapsed { padding: 0 16px; justify-content: center; }

.brand-logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: linear-gradient(135deg, #14b8a6, #0d9488);
  font-size: 18px;
  flex-shrink: 0;
}

.brand-text {
  font-size: 16px;
  white-space: nowrap;
  overflow: hidden;
  transition: opacity 0.2s;
}

.user-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 12px 4px 4px;
  border-radius: 999px;
  cursor: pointer;
  transition: background 0.15s;
}
.user-chip:hover { background: rgba(20, 184, 166, 0.1); }

.user-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: linear-gradient(135deg, #14b8a6, #0d9488);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 600;
}
.user-label {
  font-size: 13px;
  color: var(--n-text-color);
}
</style>
