<script setup lang="ts">
defineProps<{ title: string; subtitle?: string }>()
</script>

<template>
  <header class="px-8 pt-7 pb-4 flex items-end justify-between gap-4 flex-wrap">
    <div>
      <h1 class="text-2xl font-semibold tracking-tight">{{ title }}</h1>
      <p v-if="subtitle" class="text-sm text-ink-400 mt-1">{{ subtitle }}</p>
    </div>
    <div class="flex items-center gap-2"><slot /></div>
  </header>
</template>
