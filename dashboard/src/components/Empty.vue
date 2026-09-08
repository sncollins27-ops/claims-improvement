<script setup lang="ts">
defineProps<{ title: string; hint?: string }>()
</script>

<template>
  <div class="card card-pad text-center py-10">
    <div class="text-ink-200 font-medium">{{ title }}</div>
    <div v-if="hint" class="text-sm text-ink-400 mt-1 mono">{{ hint }}</div>
  </div>
</template>
