<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import Kpi from '../components/Kpi.vue'
import Empty from '../components/Empty.vue'
import { useRunsStore } from '../stores/runs'
import { fmtNum, fmtUsd, fmtSeconds } from '../api'
import { VChart, baseAxis, tooltipStyle, palette } from '../lib/echarts'

const store = useRunsStore()
const runName = ref('')
const runId = ref('run_20260907_063159_471c07')
const data = ref<any>(null)
const coverage = ref<any>(null)
const loading = ref(false)
const error = ref('')

async function load(refresh = false) {
  if (!runName.value) return
  loading.value = true
  error.value = ''
  try {
    const res = await fetch(`/api/benchmark/${runName.value}?run_id=${encodeURIComponent(runId.value)}${refresh ? '&refresh=true' : ''}`)
    if (!res.ok) throw new Error((await res.text()).slice(0, 200))
    data.value = await res.json()
    try {
      const cov = await fetch(`/api/coverage/${runName.value}`)
      coverage.value = cov.ok ? await cov.json() : null
    } catch { coverage.value = null }
  } catch (e: any) {
    error.value = String(e?.message ?? e)
    data.value = null
  } finally {
    loading.value = false
  }
}
onMounted(async () => {
  await store.refresh()
  runName.value = store.experiments.find((r) => r.name.includes('50paper'))?.name ?? store.experiments[0]?.name ?? ''
  load()
})
watch(runName, () => load())

const papers = computed<any[]>(() => (data.value?.papers ?? []).filter((p: any) => p.local_claims))
const ranking = computed<any[]>(() => data.value?.real_ranking ?? [])
const projections = computed<[string, number][]>(() => Object.entries(data.value?.projected_batch_score ?? {}) as [string, number][])
const bestProjection = computed(() => projections.value.find(([k]) => k.includes('median of real'))?.[1] ?? 0)
const winner = computed(() => ranking.value.find((r) => r.winner))

const chartOption = computed(() => {
  const rows = [...papers.value].sort((a, b) => a.real_coverage_median - b.real_coverage_median)
  return {
    tooltip: { trigger: 'axis', ...tooltipStyle },
    legend: { data: ['real coverage (median)', 'real coverage (best)', 'our quality'], textStyle: { color: '#8b98b8' }, top: 0 },
    grid: { left: 44, right: 20, top: 34, bottom: 80 },
    xAxis: baseAxis({ type: 'category', data: rows.map((r) => r.paper_id.replace('openalex_', '')), axisLabel: { color: '#8b98b8', rotate: 55, fontSize: 9 } }),
    yAxis: baseAxis({ type: 'value', max: 1 }),
    series: [
      { name: 'real coverage (median)', type: 'bar', data: rows.map((r) => r.real_coverage_median), itemStyle: { color: palette[3], borderRadius: [4, 4, 0, 0] } },
      { name: 'real coverage (best)', type: 'line', data: rows.map((r) => r.real_coverage_max), smooth: true, itemStyle: { color: palette[1] } },
      { name: 'our quality', type: 'line', data: rows.map((r) => r.local_quality ?? 0), smooth: true, itemStyle: { color: palette[0] }, lineStyle: { type: 'dashed' } },
    ],
  }
})
</script>

<template>
  <div>
    <PageHeader title="Benchmark" subtitle="Our local artifacts against the Silver scores the validator actually gave on the same batch.">
      <select v-model="runName" class="input">
        <option v-for="r in store.experiments" :key="r.name" :value="r.name">{{ r.name }}</option>
      </select>
      <input v-model="runId" class="input w-72" placeholder="mainnet run_id" />
      <button class="btn" :disabled="loading" @click="load(true)"><RefreshCw class="h-4 w-4" /> {{ loading ? 'building…' : 'Rebuild' }}</button>
    </PageHeader>

    <Empty v-if="error" title="No comparison yet" :hint="error" class="mx-8" />

    <template v-if="data">
      <section class="px-8 grid grid-cols-2 xl:grid-cols-6 gap-4">
        <Kpi label="Papers scored by validator" :value="`${data.eligible_papers} / ${data.expected_papers}`" :hint="`${data.validator_failed_papers} dropped for everyone`" />
        <Kpi label="Ours in that scored set" :value="data.local_papers_overlapping_scored" :hint="`${data.local_papers_run} run locally`" tone="info" />
        <Kpi label="Mean claims / paper" :value="data.local_mean_claims" tone="ok" />
        <Kpi label="Our quality (diag × adj)" :value="fmtNum(data.local_mean_quality, 3)" :hint="data.local_total_judge ? `${data.local_total_judge.accept}✓ ${data.local_total_judge.weak}~ ${data.local_total_judge.reject}✗` : ''" :tone="data.local_mean_quality >= 0.95 ? 'ok' : 'warn'" />
        <Kpi label="Cost / time" :value="fmtUsd(data.local_total_cost_usd)" :hint="`${fmtSeconds(data.local_mean_seconds)} per paper`" />
        <Kpi label="Real winner" :value="winner ? fmtNum(winner.batch_score, 4) : '—'" :hint="winner ? `uid ${winner.uid} · ${winner.selection_lane}` : ''" tone="muted" />
      </section>

      <section v-if="coverage" class="px-8 mt-6">
        <div class="card card-pad">
          <div class="flex items-start justify-between gap-4 flex-wrap mb-3">
            <div>
              <h2 class="font-semibold">Measured coverage of the units that already existed</h2>
              <p class="text-[12px] text-ink-400 max-w-3xl mt-1">
                Bronze reference reconstructed with the subnet's own reference miner, judged with the validator's comparator wording, scored with the validator's own functions.
                Units our own accepted claims would create are excluded on purpose, so this is a floor.
              </p>
            </div>
            <div class="flex gap-3">
              <div class="text-right"><div class="kpi-label">mean coverage</div><div class="text-2xl font-semibold text-mint-400 tabular-nums">{{ fmtNum(coverage.mean_coverage, 3) }}</div></div>
              <div class="text-right"><div class="kpi-label">estimated batch</div><div class="text-2xl font-semibold text-accent-300 tabular-nums">{{ fmtNum(coverage.estimated_batch_score, 4) }}</div></div>
              <div class="text-right"><div class="kpi-label">units matched</div><div class="text-2xl font-semibold tabular-nums">{{ coverage.matched_units_total }}/{{ coverage.bronze_units_total }}</div></div>
            </div>
          </div>
          <table class="table">
            <thead><tr><th>paper</th><th>bronze units</th><th>matched</th><th>our coverage</th><th>real median</th><th>real best</th><th>our quality</th><th>score</th><th>missed units</th></tr></thead>
            <tbody>
              <tr v-for="p in coverage.papers" :key="p.paper_id">
                <td class="mono text-[11px]">{{ p.paper_id }}</td>
                <td class="tabular-nums">{{ p.bronze_units }}</td>
                <td class="tabular-nums">{{ p.matched_units }}</td>
                <td><span class="pill" :class="p.coverage >= 0.9 ? 'pill-ok' : p.coverage >= 0.7 ? 'pill-warn' : 'pill-bad'">{{ fmtNum(p.coverage, 3) }}</span></td>
                <td class="tabular-nums">{{ fmtNum(p.real_coverage_median_scoring, 3) }}</td>
                <td class="tabular-nums">{{ fmtNum(p.real_coverage_max, 3) }}</td>
                <td class="tabular-nums">{{ fmtNum(p.measured_quality, 2) }}</td>
                <td><span class="pill" :class="(p.score_with_measured_quality ?? 0) >= 0.9 ? 'pill-ok' : 'pill-warn'">{{ fmtNum(p.score_with_measured_quality, 3) }}</span></td>
                <td class="text-[11px] text-ink-400 max-w-md">{{ (p.missing_units || []).join(' · ') }}</td>
              </tr>
            </tbody>
          </table>
          <ul class="mt-3 text-[11px] text-ink-500 list-disc ml-5">
            <li v-for="(c, i) in coverage.caveats" :key="i">{{ c }}</li>
          </ul>
        </div>
      </section>

      <section class="px-8 mt-6 grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div class="card card-pad xl:col-span-2">
          <h2 class="font-semibold mb-2">Coverage the real miners achieved, per paper</h2>
          <p class="text-[12px] text-ink-400 mb-2">Bars are the median miner's coverage; the green line is the best miner's. Our dashed line is our measured quality, not coverage: only the validator can compute true coverage against its Silver units.</p>
          <VChart v-if="papers.length" :option="chartOption" autoresize style="height: 320px" />
        </div>
        <div class="space-y-4">
          <div class="card card-pad">
            <h2 class="font-semibold mb-2">Projected batch score</h2>
            <p class="text-[12px] text-ink-400 mb-3">Our measured quality multiplied by an assumed coverage. Assumptions are named, not guessed away.</p>
            <div v-for="[label, value] in projections" :key="label" class="flex items-center justify-between py-1.5 border-b border-ink-800 last:border-0">
              <span class="text-[12px] text-ink-300">{{ label }}</span>
              <span class="pill" :class="value >= (winner?.batch_score ?? 1) ? 'pill-ok' : 'pill-warn'">{{ fmtNum(value, 4) }}</span>
            </div>
            <div v-if="winner" class="mt-3 text-[12px] text-ink-400">
              Winner on this batch scored <span class="text-ink-100">{{ fmtNum(winner.batch_score, 4) }}</span>.
              Our median-coverage projection is <span :class="bestProjection >= winner.batch_score ? 'text-mint-400' : 'text-amber-400'">{{ fmtNum(bestProjection, 4) }}</span>.
            </div>
          </div>
          <div class="card card-pad">
            <h2 class="font-semibold mb-2">Real ranking</h2>
            <table class="table">
              <thead><tr><th>#</th><th>uid</th><th>batch</th><th>median</th><th>lane</th></tr></thead>
              <tbody>
                <tr v-for="r in ranking" :key="r.uid">
                  <td class="tabular-nums">{{ r.rank }}<span v-if="r.winner"> 🏆</span></td>
                  <td class="mono text-xs">{{ r.uid }}</td>
                  <td class="tabular-nums">{{ fmtNum(r.batch_score, 4) }}</td>
                  <td class="tabular-nums">{{ fmtNum(r.median_score, 3) }}</td>
                  <td class="text-[11px] text-ink-400">{{ r.selection_lane }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section class="px-8 mt-6 pb-10">
        <div class="card overflow-x-auto">
          <table class="table">
            <thead><tr><th>paper</th><th>our claims</th><th>our diag</th><th>our judge</th><th>our quality</th><th>real cov median</th><th>real cov best</th><th>real best score</th><th>real median score</th><th>miners scoring</th></tr></thead>
            <tbody>
              <tr v-for="p in papers" :key="p.paper_id">
                <td class="max-w-md"><div class="text-ink-100">{{ p.title || p.paper_id }}</div><div class="mono text-[11px] text-ink-400">{{ p.paper_id }}</div></td>
                <td class="tabular-nums font-semibold">{{ p.local_claims }}</td>
                <td class="tabular-nums">{{ fmtNum(p.local_diagnostic_quality, 2) }}</td>
                <td class="mono text-xs">{{ p.local_judge ? `${p.local_judge.accept ?? 0}/${p.local_judge.weak ?? 0}/${p.local_judge.reject ?? 0}` : '—' }}</td>
                <td><span class="pill" :class="(p.local_quality ?? 0) >= 0.95 ? 'pill-ok' : 'pill-warn'">{{ fmtNum(p.local_quality, 3) }}</span></td>
                <td class="tabular-nums">{{ fmtNum(p.real_coverage_median, 3) }}</td>
                <td class="tabular-nums">{{ fmtNum(p.real_coverage_max, 3) }}</td>
                <td class="tabular-nums">{{ fmtNum(p.real_score_max, 3) }}</td>
                <td class="tabular-nums">{{ fmtNum(p.real_score_median, 3) }}</td>
                <td class="tabular-nums">{{ p.real_scoring_miner_count }}/{{ p.real_miner_count }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>
