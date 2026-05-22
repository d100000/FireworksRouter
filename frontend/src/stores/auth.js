import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '@/api'

const KEY_SESSION = 'fwr_session_token'

export const useAuthStore = defineStore('auth', () => {
  const sessionToken = ref(localStorage.getItem(KEY_SESSION) || '')

  const isLoggedIn = computed(() => !!sessionToken.value)
  // 单租户管理端：登录即管理员
  const isAdmin = computed(() => !!sessionToken.value)

  async function login(password) {
    const { data } = await authApi.login({ password })
    sessionToken.value = data.session_token
    localStorage.setItem(KEY_SESSION, data.session_token)
    return data
  }

  function logout() {
    sessionToken.value = ''
    localStorage.removeItem(KEY_SESSION)
  }

  // 兼容旧代码：提供 accessToken 别名给 axios interceptor
  const accessToken = computed(() => sessionToken.value)
  const user = computed(() => isLoggedIn.value ? { role: 'admin', display_name: 'Admin' } : null)

  return { sessionToken, accessToken, user, isLoggedIn, isAdmin, login, logout }
})
