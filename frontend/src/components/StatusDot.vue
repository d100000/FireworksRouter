<template>
  <span class="fwr-status-dot" :class="[level, { pulse }]">
    <slot>{{ label }}</slot>
  </span>
</template>

<script setup>
const props = defineProps({
  status: { type: String, default: '' },
  label: { type: String, default: '' },
  pulse: { type: Boolean, default: false },
})

import { computed } from 'vue'
const level = computed(() => {
  const s = props.status.toLowerCase()
  if (['active', 'ready', 'success', 'ok', 'healthy'].includes(s)) return 'success'
  if (['testing', 'pending', 'refreshing', 'info'].includes(s)) return 'info'
  if (['unhealthy', 'warning', 'cooldown', 'expired'].includes(s)) return 'warning'
  if (['disabled', 'auto_disabled', 'blocked', 'error', 'failed'].includes(s)) return 'danger'
  return ''
})
</script>
