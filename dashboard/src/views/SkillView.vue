<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import PageHeader from '../components/PageHeader.vue'
import { api } from '../api'

const skill = ref<any>(null)
const active = ref('SKILL.md')
onMounted(async () => { skill.value = await api.skill() })
const current = computed(() => skill.value?.resources?.find((r: any) => r.path === active.value))
</script>

<template>
  <div>
    <PageHeader title="Skill pack" :subtitle="skill ? `${skill.name} · sha256 ${skill.sha256.slice(0, 16)}… · ${skill.root_dir}` : 'loading…'" />
    <section class="px-8 pb-10 grid grid-cols-1 xl:grid-cols-4 gap-4">
      <div class="card card-pad">
        <div class="kpi-label mb-2">files</div>
        <button v-for="r in skill?.resources ?? []" :key="r.path" class="w-full text-left rounded-lg px-3 py-2 text-sm mono transition-colors" :class="active === r.path ? 'bg-accent-500/15 text-accent-300' : 'text-ink-300 hover:bg-ink-800'" @click="active = r.path">
          {{ r.path }}<div class="text-[10px] text-ink-500">{{ r.bytes }} B · {{ r.sha256.slice(0, 10) }}</div>
        </button>
        <div v-if="skill?.available" class="mt-4 text-[11px] text-ink-400">available packs: {{ skill.available.join(', ') }}</div>
        <div v-if="skill?.metadata?.version" class="mt-2 text-[11px] text-ink-400">version {{ skill.metadata.version }}</div>
      </div>
      <pre class="card card-pad xl:col-span-3 text-[12px] mono text-ink-200 whitespace-pre-wrap max-h-[80vh] overflow-auto leading-relaxed">{{ current?.text ?? '' }}</pre>
    </section>
  </div>
</template>
