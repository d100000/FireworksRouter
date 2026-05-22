import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'

const KEY = 'fwr_theme'  // 'light' | 'dark'

export const useThemeStore = defineStore('theme', () => {
  const mode = ref(localStorage.getItem(KEY) || 'light')
  const isDark = computed(() => mode.value === 'dark')

  function toggle() {
    mode.value = isDark.value ? 'light' : 'dark'
  }

  function set(v) {
    mode.value = v
  }

  watch(mode, (v) => {
    localStorage.setItem(KEY, v)
  })

  return { mode, isDark, toggle, set }
})
