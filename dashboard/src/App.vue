<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { Activity, BarChart3, BookOpenText, FlaskConical, GitCompare, ListChecks, Network, Sparkles } from 'lucide-vue-next'
import { api } from './api'

const route = useRoute()
const status = ref<any>(null)
const nav = [
  { to: '/', label: 'Overview', icon: Activity },
  { to: '/runs', label: 'Runs', icon: FlaskConical },
  { to: '/compare', label: 'Compare', icon: GitCompare },
  { to: '/benchmark', label: 'Benchmark', icon: BarChart3 },
  { to: '/network', label: 'Network', icon: Network },
  { to: '/skill', label: 'Skill', icon: BookOpenText },
  { to: '/agenda', label: 'Agenda', icon: ListChecks },
]
async function refresh() {
  try { status.value = await api.status() } catch { status.value = null }
}
onMounted(() => { refresh(); setInterval(refresh, 30000) })
function isActive(to: string) {
  if (to === '/') return route.path === '/'
  return route.path.startsWith(to)
}
</script>

<template>
  <div class="min-h-full flex">
    <aside class="w-60 shrink-0 border-r border-ink-800 bg-ink-900/70 backdrop-blur flex flex-col">
      <div class="px-5 pt-5 pb-4 border-b border-ink-800">
        <div class="flex items-center gap-2">
          <div class="h-8 w-8 rounded-xl bg-gradient-to-br from-accent-500 to-violet-400 grid place-items-center shadow-lg shadow-accent-500/30">
            <Sparkles class="h-4 w-4 text-white" />
          </div>
          <div>
            <div class="text-sm font-semibold leading-tight">Claims Console</div>
            <div class="text-[11px] text-ink-400">improvement lab · SN111</div>
          </div>
        </div>
      </div>
      <nav class="p-3 flex flex-col gap-1">
        <RouterLink v-for="item in nav" :key="item.to" :to="item.to"
          class="flex items-center gap-3 rounded-xl px-3 py-2 text-sm transition-colors"
          :class="isActive(item.to) ? 'bg-accent-500/15 text-accent-300 border border-accent-500/30' : 'text-ink-300 hover:bg-ink-800 hover:text-ink-100 border border-transparent'">
          <component :is="item.icon" class="h-4 w-4" />
          {{ item.label }}
        </RouterLink>
      </nav>
      <div class="mt-auto p-4 text-[11px] text-ink-400 border-t border-ink-800 space-y-1">
        <div class="flex items-center gap-2">
          <span class="h-2 w-2 rounded-full" :class="status ? 'bg-mint-400 shadow-[0_0_8px_rgba(61,220,151,0.8)]' : 'bg-rose-400'"></span>
          <span>{{ status ? 'sidecar online' : 'sidecar offline' }}</span>
        </div>
        <div v-if="status" class="mono truncate" :title="status.root">{{ status.root }}</div>
        <div v-if="status?.env?.SUBNET_CLAIMS_AGENT_MODEL" class="truncate">model {{ status.env.SUBNET_CLAIMS_AGENT_MODEL }}</div>
        <div v-if="status?.env?.SUBNET_CLAIMS_AGENT_RUNTIME">runtime {{ status.env.SUBNET_CLAIMS_AGENT_RUNTIME }}</div>
        <div v-if="status?.processes?.length" class="text-mint-400">{{ status.processes.length }} miner process(es) running</div>
      </div>
    </aside>
    <main class="flex-1 min-w-0">
      <RouterView v-slot="{ Component }">
        <Transition name="fade" mode="out-in">
          <component :is="Component" />
        </Transition>
      </RouterView>
    </main>
  </div>
</template>
