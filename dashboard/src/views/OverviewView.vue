<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { RefreshCw } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import Kpi from '../components/Kpi.vue'
import Empty from '../components/Empty.vue'
import { useRunsStore } from '../stores/runs'
import { api, fmtNum, fmtUsd, fmtInt, ago, qualityClass, type AgendaItem } from '../api'
import { VChart, baseAxis, tooltipStyle, palette } from '../lib/echarts'

const store = useRunsStore()
const network = ref<any>(null)
const agenda = ref<AgendaItem[]>([])
const loadingNet = ref(false)

async function load() {
  store.refresh()
  loadingNet.value = true
  try {
    const [net, ag] = await Promise.all([api.networkOverview(), api.agenda()])
    network.value = net
    agenda.value = ag.items
  } catch (e) {
    console.error(e)
  } finally {
    loadingNet.value = false
  }
}
onMounted(load)

const latestExperiment = computed(() => store.experiments[0])
const experimentSeries = computed(() => {
  const rows = [...store.experiments].reverse().slice(-12)
  return {
    names: rows.map((r) => r.name),
    claims: rows.map((r) => r.avg_claims ?? 0),
    quality: rows.map((r) => (r.avg_quality ?? 0) * 100),
    cost: rows.map((r) => r.total_cost_usd ?? 0),
  }
})
const chartOption = computed(() => ({
  tooltip: { trigger: 'axis', ...tooltipStyle },
  legend: { data: ['avg claims', 'quality est. %'], textStyle: { color: '#8b98b8' }, top: 0 },
  grid: { left: 40, right: 40, top: 34, bottom: 60 },
  xAxis: baseAxis({ type: 'category', data: experimentSeries.value.names, axisLabel: { color: '#8b98b8', rotate: 25, fontSize: 10 } }),
  yAxis: [baseAxis({ type: 'value', name: 'claims' }), baseAxis({ type: 'value', name: '%', max: 100, splitLine: { show: false } })],
  series: [
    { name: 'avg claims', type: 'bar', data: experimentSeries.value.claims, itemStyle: { color: palette[0], borderRadius: [6, 6, 0, 0] }, barMaxWidth: 34 },
    { name: 'quality est. %', type: 'line', yAxisIndex: 1, data: experimentSeries.value.quality, smooth: true, itemStyle: { color: palette[1] }, lineStyle: { width: 2 } },
  ],
}))

const me = computed(() => network.value?.me)
const latestNetRun = computed(() => (network.value?.runs ?? [])[0])
const openAgenda = computed(() => agenda.value.filter((i) => i.status !== 'done').slice(0, 6))
const myLatestReport = computed(() => me.value?.reports?.[0])
const winnerText = computed(() => {
  const run = latestNetRun.value
  if (!run) return '—'
  return `top ${fmtNum(run.silver_batch_top_score, 3)} · avg ${fmtNum(run.silver_average_score, 3)}`
})
</script>

<template>
  <div>
    <PageHeader title="Overview" subtitle="Local extraction experiments, the live miner, and what to do next.">
      <button class="btn" @click="load"><RefreshCw class="h-4 w-4" /> Refresh</button>
    </PageHeader>

    <section class="px-8 grid grid-cols-2 xl:grid-cols-5 gap-4">
      <Kpi label="Latest experiment" :value="latestExperiment?.name ?? '—'" :hint="latestExperiment ? `${latestExperiment.completed}/${latestExperiment.paper_count} papers · ${ago(latestExperiment.updated_at)}` : 'run tools/run_paper.py'" tone="info" />
      <Kpi label="Avg claims / paper" :value="latestExperiment?.avg_claims ?? '—'" hint="winners: 40-100 per paper" :tone="(latestExperiment?.avg_claims ?? 0) >= 40 ? 'ok' : 'warn'" />
      <Kpi label="Quality estimate" :value="latestExperiment?.avg_quality != null ? fmtNum(latestExperiment.avg_quality, 3) : '—'" hint="diagnostic × adjudication (local)" :tone="(latestExperiment?.avg_quality ?? 0) >= 0.9 ? 'ok' : 'warn'" />
      <Kpi label="My miner (UID 192)" :value="me ? fmtNum(me.latest_score, 3) : '—'" :hint="myLatestReport ? `${myLatestReport.run_id} · best ${fmtNum(me.best_score, 3)}` : 'no report yet'" :tone="(me?.latest_score ?? 0) > 0.6 ? 'ok' : 'bad'" />
      <Kpi label="Mainnet latest run" :value="winnerText" :hint="latestNetRun ? `${latestNetRun.run_id} · ${ago(latestNetRun.ended_at)}` : ''" tone="muted" />
    </section>

    <section class="px-8 mt-6 grid grid-cols-1 xl:grid-cols-3 gap-4">
      <div class="card card-pad xl:col-span-2">
        <div class="flex items-center justify-between mb-2">
          <h2 class="font-semibold">Experiment trajectory</h2>
          <RouterLink to="/runs" class="text-xs text-accent-300 hover:underline">all runs →</RouterLink>
        </div>
        <VChart v-if="experimentSeries.names.length" :option="chartOption" autoresize style="height: 280px" />
        <Empty v-else title="No experiments yet" hint=".venv/bin/python -m tools.run_paper --pdf papers/<id>.pdf --paper-id <id> --run-name staged-v2 --judge" />
      </div>
      <div class="card card-pad">
        <div class="flex items-center justify-between mb-3">
          <h2 class="font-semibold">Next steps</h2>
          <RouterLink to="/agenda" class="text-xs text-accent-300 hover:underline">agenda →</RouterLink>
        </div>
        <ul class="space-y-2">
          <li v-for="item in openAgenda" :key="item.id" class="flex gap-2 text-sm">
            <span class="pill" :class="item.priority === 'p0' ? 'pill-bad' : item.priority === 'p1' ? 'pill-warn' : 'pill-muted'">{{ item.priority }}</span>
            <span class="text-ink-200">{{ item.title }}</span>
          </li>
          <li v-if="!openAgenda.length" class="text-sm text-ink-400">Nothing open. Add items in Agenda.</li>
        </ul>
      </div>
    </section>

    <section class="px-8 mt-6 mb-10 grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div class="card card-pad">
        <div class="flex items-center justify-between mb-3">
          <h2 class="font-semibold">Recent local runs</h2>
        </div>
        <table class="table">
          <thead><tr><th>run</th><th>papers</th><th>avg claims</th><th>quality</th><th>cost</th><th>updated</th></tr></thead>
          <tbody>
            <tr v-for="run in store.runs.slice(0, 8)" :key="run.name">
              <td><RouterLink :to="`/runs?run=${encodeURIComponent(run.name)}`" class="text-accent-300 hover:underline mono text-xs">{{ run.name }}</RouterLink></td>
              <td class="tabular-nums">{{ run.completed }}/{{ run.paper_count }}<span v-if="run.failed" class="text-rose-400"> ({{ run.failed }} failed)</span></td>
              <td class="tabular-nums">{{ run.avg_claims ?? '—' }}</td>
              <td><span class="pill" :class="qualityClass(run.avg_quality)">{{ run.avg_quality != null ? fmtNum(run.avg_quality, 3) : '—' }}</span></td>
              <td class="tabular-nums">{{ fmtUsd(run.total_cost_usd) }}</td>
              <td class="text-ink-400">{{ ago(run.updated_at) }}</td>
            </tr>
            <tr v-if="!store.runs.length"><td colspan="6" class="text-ink-400">No runs found under runs/.</td></tr>
          </tbody>
        </table>
      </div>
      <div class="card card-pad">
        <div class="flex items-center justify-between mb-3">
          <h2 class="font-semibold">Mainnet runs</h2>
          <RouterLink to="/network" class="text-xs text-accent-300 hover:underline">network →</RouterLink>
        </div>
        <table class="table">
          <thead><tr><th>run</th><th>ended</th><th>miners</th><th>avg</th><th>top</th><th>me</th></tr></thead>
          <tbody>
            <tr v-for="run in (network?.runs ?? []).slice(0, 8)" :key="run.run_id">
              <td><RouterLink :to="`/network/runs/${run.run_id}`" class="text-accent-300 hover:underline mono text-xs">{{ run.run_display_id ?? run.run_id }}</RouterLink></td>
              <td class="text-ink-400">{{ ago(run.ended_at) }}</td>
              <td class="tabular-nums">{{ run.response_count }}/{{ (run.target_uids ?? []).length }}</td>
              <td class="tabular-nums">{{ fmtNum(run.silver_average_score, 3) }}</td>
              <td class="tabular-nums">{{ fmtNum(run.silver_batch_top_score, 3) }}</td>
              <td><span v-if="(run.target_uids ?? []).includes(network?.my_uid)" class="pill pill-info">selected</span><span v-else class="text-ink-500">—</span></td>
            </tr>
            <tr v-if="loadingNet"><td colspan="6" class="text-ink-400">Loading network…</td></tr>
          </tbody>
        </table>
        <div v-if="network?.stats" class="mt-3 text-[11px] text-ink-400 flex gap-4 flex-wrap">
          <span v-for="(v, k) in network.stats" :key="k">{{ k }}: <span class="text-ink-200">{{ typeof v === 'number' ? fmtInt(v) : v }}</span></span>
        </div>
      </div>
    </section>
  </div>
</template>
