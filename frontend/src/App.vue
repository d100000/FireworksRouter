<template>
  <n-config-provider
    :theme="theme"
    :theme-overrides="themeOverrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <n-message-provider>
      <n-dialog-provider>
        <n-notification-provider>
          <router-view />
        </n-notification-provider>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<script setup>
import { computed } from 'vue'
import { darkTheme, zhCN, dateZhCN } from 'naive-ui'
import { useThemeStore } from '@/stores/theme'

const themeStore = useThemeStore()
const theme = computed(() => (themeStore.isDark ? darkTheme : null))

// Sub2api 风格的 Teal 主色 + 重圆角 + 紧凑间距
const themeOverrides = computed(() => {
  const primary500 = '#14b8a6'
  const primary600 = '#0d9488'
  const primary400 = '#2dd4bf'
  const primary50 = '#f0fdfa'

  return {
    common: {
      primaryColor: primary500,
      primaryColorHover: primary400,
      primaryColorPressed: primary600,
      primaryColorSuppl: primary500,
      fontFamily: "system-ui, -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif",
      fontFamilyMono: "ui-monospace, 'SF Mono', Menlo, Monaco, Consolas, 'Roboto Mono', monospace",
      borderRadius: '10px',
      borderRadiusSmall: '6px',
      lineHeight: '1.6',
      bodyColor: themeStore.isDark ? '#020617' : '#f8fafc',
      cardColor: themeStore.isDark ? '#0f172a' : '#ffffff',
      modalColor: themeStore.isDark ? '#0f172a' : '#ffffff',
      popoverColor: themeStore.isDark ? '#1e293b' : '#ffffff',
      tableColor: themeStore.isDark ? '#0f172a' : '#ffffff',
      tableHeaderColor: themeStore.isDark ? '#1e293b' : '#f8fafc',
      hoverColor: themeStore.isDark ? '#1e293b' : '#f1f5f9',
      dividerColor: themeStore.isDark ? '#334155' : '#e2e8f0',
      borderColor: themeStore.isDark ? '#334155' : '#e2e8f0',
      textColorBase: themeStore.isDark ? '#f1f5f9' : '#0f172a',
      placeholderColor: themeStore.isDark ? '#64748b' : '#94a3b8',
      successColor: '#22c55e',
      warningColor: '#f59e0b',
      errorColor: '#ef4444',
      infoColor: '#3b82f6',
    },
    Card: {
      borderRadius: '16px',
      paddingMedium: '20px',
      paddingSmall: '14px',
    },
    Button: {
      borderRadiusMedium: '10px',
      borderRadiusSmall: '8px',
      heightMedium: '36px',
    },
    Tag: {
      borderRadius: '6px',
      heightSmall: '20px',
    },
    DataTable: {
      borderRadius: '12px',
      thColor: themeStore.isDark ? '#1e293b' : '#f8fafc',
      tdColorHover: themeStore.isDark ? '#1e293b' : '#f8fafc',
      borderColor: themeStore.isDark ? '#1e293b' : '#e2e8f0',
    },
    Menu: {
      borderRadius: '10px',
      itemHeight: '40px',
      itemColorActive: themeStore.isDark ? 'rgba(20, 184, 166, 0.16)' : primary50,
      itemColorActiveHover: themeStore.isDark ? 'rgba(20, 184, 166, 0.22)' : '#ccfbf1',
      itemTextColorActive: themeStore.isDark ? '#5eead4' : primary600,
      itemTextColorActiveHover: themeStore.isDark ? '#5eead4' : primary600,
      itemIconColorActive: themeStore.isDark ? '#5eead4' : primary600,
      itemIconColorActiveHover: themeStore.isDark ? '#5eead4' : primary600,
    },
    Input: {
      borderRadius: '10px',
      heightMedium: '38px',
    },
    Select: {
      peers: {
        InternalSelection: { borderRadius: '10px', heightMedium: '38px' },
      },
    },
    Switch: {
      railColorActive: primary500,
    },
    Statistic: {
      valueFontSize: '24px',
    },
  }
})
</script>
