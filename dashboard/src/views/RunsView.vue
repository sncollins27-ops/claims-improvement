<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { ChevronDown, ChevronRight, Copy, RefreshCw } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import Empty from '../components/Empty.vue'
import { useRunsStore } from '../stores/runs'
import { fmtNum, fmtUsd, fmtSeconds, ago, qualityClass, type RunRow } from '../api'
import { copyText } from '../lib/text'

const store = useRunsStore()
const route = useRoute()
const open = ref<Record<string, boolean>>({})
const filter = ref('')
const copied = ref('')

onMounted(async () => {
  await store.refresh()
  const target = route.query.run as string | undefined
  if (target) open.value[target] = true
  else if (store.runs[0]) open.value[store.runs[0].name] = true
})
watch(() => route.query.run, (v) => { if (v) open.value[String(v)] = true })

const visible = computed(() => store.runs.filter((r) => !filter.value || r.name.includes(filter.value) || r.papers.some((p) => p.title?.toLowerCase().includes(filter.value.toLowerCase()) || p.paper_id.includes(filter.value))))

function rerunCommand(run: RunRow, paperId: string) {
  const p = run.papers.find((x) => x.paper_id === paperId)
  const runtime = p?.runtime ? ` --runtime ${p.runtime}` : ''
  return `.venv/bin/python -m tools.run_paper --pdf papers/${paperId}.pdf --paper-id ${paperId} --run-name ${run.name}-next${runtime} --judge --force`
}
async function copy(cmd: string) {
  if (await copyText(cmd)) { copied.value = cmd; setTimeout(() => (copied.value = ''), 1500) }
}
function verdictText(p: any) {
  const v = p.judge?.verdicts
  if (!v) return '—'
  return `${v.accept ?? 0}✓ ${v.weak ?? 0}~ ${v.reject ?? 0}✗`
}
</script>

<template>
  <div>
    <PageHeader title="Runs" subtitle="Every local experiment and live neuron task under runs/. Click a paper to audit its artifact.">
      <input v-model="filter" class="input w-64" placeholder="filter by run, paper id or title" />
      <button class="btn" @click="store.refresh()"><RefreshCw class="h-4 w-4" /> Refresh</button>
    </PageHeader>

    <section class="px-8 pb-10 space-y-4">
      <Empty v-if="!store.loading && !visible.length" title="No runs" hint=".venv/bin/python -m tools.run_paper --pdf papers/<id>.pdf --paper-id <id> --run-name staged-v2 --judge" />
      <div v-for="run in visible" :key="run.name" class="card">
        <button class="w-full flex items-center gap-3 px-5 py-4 text-left" @click="open[run.name] = !open[run.name]">
          <component :is="open[run.name] ? ChevronDown : ChevronRight" class="h-4 w-4 text-ink-400" />
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="font-semibold mono">{{ run.name }}</span>
              <span class="pill" :class="run.kind === 'neuron' ? 'pill-info' : 'pill-muted'">{{ run.kind }}</span>
              <span v-if="run.runtime" class="pill pill-muted">{{ run.runtime }}</span>
              <span v-if="run.model" class="text-[11px] text-ink-400 mono truncate max-w-xs">{{ run.model }}</span>
            </div>
          </div>
          <div class="grid grid-cols-5 gap-6 text-sm tabular-nums shrink-0">
            <div><div class="kpi-label">papers</div><div>{{ run.completed }}/{{ run.paper_count }}<span v-if="run.failed" class="text-rose-400"> · {{ run.failed }} failed</span></div></div>
            <div><div class="kpi-label">avg claims</div><div :class="(run.avg_claims ?? 0) >= 40 ? 'text-mint-400' : 'text-amber-400'">{{ run.avg_claims ?? '—' }}</div></div>
            <div><div class="kpi-label">quality est.</div><div><span class="pill" :class="qualityClass(run.avg_quality)">{{ run.avg_quality != null ? fmtNum(run.avg_quality, 3) : '—' }}</span></div></div>
            <div><div class="kpi-label">cost</div><div>{{ fmtUsd(run.total_cost_usd) }}</div></div>
            <div><div class="kpi-label">updated</div><div class="text-ink-300">{{ ago(run.updated_at) }}</div></div>
          </div>
        </button>
        <div v-if="open[run.name]" class="border-t border-ink-800 overflow-x-auto">
          <table class="table">
            <thead><tr><th>paper</th><th>status</th><th>claims</th><th>evidence</th><th>exp</th><th>span cov.</th><th>diag</th><th>judge</th><th>quality</th><th>cost</th><th>time</th><th></th></tr></thead>
            <tbody>
              <tr v-for="p in run.papers" :key="p.paper_id">
                <td class="max-w-md">
                  <RouterLink :to="`/runs/${run.name}/paper/${p.paper_id}`" class="text-accent-300 hover:underline">{{ p.title || p.paper_id }}</RouterLink>
                  <div class="text-[11px] text-ink-400 mono">{{ p.paper_id }}<span v-if="p.note"> · {{ p.note }}</span></div>
                  <div v-if="p.error" class="text-[11px] text-rose-400 mt-1 mono">{{ p.error }}</div>
                </td>
                <td><span class="pill" :class="p.status === 'completed' ? 'pill-ok' : p.status === 'failed' ? 'pill-bad' : 'pill-warn'">{{ p.status }}</span></td>
                <td class="tabular-nums font-semibold" :class="(p.stats?.claims ?? 0) >= 40 ? 'text-mint-400' : ''">{{ p.stats?.claims ?? '—' }}</td>
                <td class="tabular-nums">{{ p.stats?.evidence_records ?? '—' }}</td>
                <td class="tabular-nums">{{ p.stats?.experiments ?? '—' }}</td>
                <td class="tabular-nums">{{ p.stats?.span_coverage != null ? `${Math.round(p.stats.span_coverage * 100)}%` : '—' }}</td>
                <td><span class="pill" :class="qualityClass(p.diagnostic_quality_estimate)">{{ p.diagnostic_quality_estimate != null ? fmtNum(p.diagnostic_quality_estimate, 2) : '—' }}</span></td>
                <td class="mono text-xs">{{ verdictText(p) }}</td>
                <td><span class="pill" :class="qualityClass(p.quality_estimate)">{{ p.quality_estimate != null ? fmtNum(p.quality_estimate, 3) : '—' }}</span></td>
                <td class="tabular-nums">{{ fmtUsd(p.cost_usd) }}</td>
                <td class="tabular-nums">{{ fmtSeconds(p.wall_seconds) }}</td>
                <td>
                  <button class="btn !px-2 !py-1" :title="rerunCommand(run, p.paper_id)" @click="copy(rerunCommand(run, p.paper_id))">
                    <Copy class="h-3.5 w-3.5" /> <span class="text-xs">{{ copied === rerunCommand(run, p.paper_id) ? 'copied' : 'rerun cmd' }}</span>
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  </div>
</template>
