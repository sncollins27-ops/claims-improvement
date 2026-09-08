<script setup lang="ts">
defineProps<{ label: string; value: string | number; hint?: string; tone?: 'ok' | 'warn' | 'bad' | 'info' | 'muted' }>()
const toneClass: Record<string, string> = {
  ok: 'text-mint-400', warn: 'text-amber-400', bad: 'text-rose-400', info: 'text-accent-300', muted: 'text-ink-100',
}
</script>

<template>
  <div class="kpi">
    <div class="kpi-label">{{ label }}</div>
    <div class="kpi-value" :class="toneClass[tone ?? 'muted']">{{ value }}</div>
    <div v-if="hint" class="text-[11px] text-ink-400">{{ hint }}</div>
  </div>
</template>
