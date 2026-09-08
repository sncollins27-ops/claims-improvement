<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { ArrowLeft } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import Kpi from '../components/Kpi.vue'
import { api, fmtNum, fmtTime } from '../api'
import { VChart, baseAxis, tooltipStyle } from '../lib/echarts'

const props = defineProps<{ runId: string }>()
const data = ref<any>(null)
const status = ref<any>(null)
const myUid = ref<number | null>(null)
async function load() {
  data.value = await api.networkRun(props.runId)
  try { status.value = await api.status(); myUid.value = status.value?.my_uid ?? null } catch { /* ignore */ }
}
onMounted(load)
watch(() => props.runId, load)

const run = computed(() => data.value?.run ?? {})
const batchScores = computed<any[]>(() => [...(data.value?.batch_scores ?? [])].sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99)))
const silver = computed<any[]>(() => data.value?.silver_scores ?? [])
const papers = computed<any[]>(() => data.value?.batch?.papers ?? [])
const paperTitle = computed<Record<string, string>>(() => Object.fromEntries(papers.value.map((p) => [p.paper_id, p.title])))
const uids = computed(() => batchScores.value.map((b) => b.uid))
const paperIds = computed(() => {
  const ids = [...new Set(silver.value.map((s) => s.paper_id))]
  const order = Object.fromEntries(papers.value.map((p, i) => [p.paper_id, p.position ?? i]))
  return ids.sort((a, b) => (order[a] ?? 999) - (order[b] ?? 999))
})
const heatOption = computed(() => ({
  tooltip: { ...tooltipStyle, formatter: (p: any) => `${paperTitle.value[paperIds.value[p.value[1]]] ?? paperIds.value[p.value[1]]}<br/>UID ${uids.value[p.value[0]]}: <b>${p.value[2]}</b>` },
  grid: { left: 60, right: 20, top: 10, bottom: 90 },
  xAxis: baseAxis({ type: 'category', data: uids.value.map((u) => `uid ${u}`), position: 'bottom', axisLabel: { color: '#8b98b8', rotate: 40 } }),
  yAxis: baseAxis({ type: 'category', data: paperIds.value.map((id, i) => `#${i + 1}`), axisLabel: { color: '#8b98b8', fontSize: 9 } }),
  visualMap: { min: 0, max: 1, orient: 'horizontal', left: 'center', bottom: 0, textStyle: { color: '#8b98b8' }, inRange: { color: ['#ff6b81', '#f5b84a', '#3ddc97'] } },
  series: [{ type: 'heatmap', data: silver.value.map((s) => [uids.value.indexOf(s.uid), paperIds.value.indexOf(s.paper_id), s.score]), label: { show: false }, emphasis: { itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.5)' } } }],
}))
const perPaperBest = computed(() => paperIds.value.map((id) => {
  const rows = silver.value.filter((s) => s.paper_id === id)
  const best = rows.reduce((a, b) => (b.score > (a?.score ?? -1) ? b : a), null as any)
  const mine = rows.find((s) => s.uid === myUid.value)
  return { id, title: paperTitle.value[id] ?? id, best, mine, n: rows.length, avg: rows.length ? rows.reduce((a, b) => a + b.score, 0) / rows.length : null }
}))
</script>

<template>
  <div>
    <PageHeader :title="run.run_display_id ?? runId" :subtitle="`${run.status} · batch ${run.batch_display_id ?? run.batch_id ?? ''} · ${fmtTime(run.started_at)} → ${fmtTime(run.ended_at)}`">
      <RouterLink class="btn" to="/network"><ArrowLeft class="h-4 w-4" /> network</RouterLink>
    </PageHeader>

    <section class="px-8 grid grid-cols-2 xl:grid-cols-6 gap-4">
      <Kpi label="papers" :value="run.paper_count ?? '—'" :hint="`${run.miner_artifact_count ?? '—'} artifacts`" />
      <Kpi label="miners" :value="`${run.response_count ?? '—'} / ${(run.target_uids ?? []).length}`" hint="responded / targeted" />
      <Kpi label="silver avg" :value="fmtNum(run.silver_average_score, 3)" />
      <Kpi label="silver median" :value="fmtNum(run.silver_median_score, 3)" />
      <Kpi label="top batch score" :value="fmtNum(run.silver_batch_top_score, 3)" tone="ok" />
      <Kpi label="paper scores" :value="run.silver_score_count ?? '—'" />
    </section>

    <section class="px-8 mt-6 grid grid-cols-1 xl:grid-cols-2 gap-4 pb-10">
      <div class="card card-pad overflow-x-auto">
        <h2 class="font-semibold mb-3">Batch scores</h2>
        <table class="table">
          <thead><tr><th>#</th><th>uid</th><th>lane</th><th>batch</th><th>mean</th><th>median</th><th>min</th><th>scored</th><th>weight</th></tr></thead>
          <tbody>
            <tr v-for="b in batchScores" :key="b.uid" :class="b.uid === myUid ? 'bg-accent-500/10' : ''">
              <td class="tabular-nums">{{ b.rank }}<span v-if="b.winner" class="ml-1">🏆</span></td>
              <td class="mono text-xs">{{ b.uid }}</td>
              <td><span class="pill pill-muted">{{ b.selection_lane }}</span><span v-if="b.newcomer" class="pill pill-info ml-1">new</span></td>
              <td class="tabular-nums font-semibold">{{ fmtNum(b.batch_score, 4) }}</td>
              <td class="tabular-nums">{{ fmtNum(b.mean_score, 3) }}</td><td class="tabular-nums">{{ fmtNum(b.median_score, 3) }}</td><td class="tabular-nums">{{ fmtNum(b.min_score, 3) }}</td>
              <td class="tabular-nums">{{ b.scored_paper_count }}/{{ b.eligible_paper_count }} <span class="text-ink-400 text-[11px]">({{ b.submitted_paper_count }} submitted)</span></td>
              <td class="tabular-nums">{{ fmtNum(b.payout_weight, 4) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="batchScores[0]?.validator_failed_paper_ids?.length" class="text-[11px] text-ink-400 mt-2">{{ batchScores[0].validator_failed_paper_ids.length }} papers failed on the validator side and were excluded for everyone.</div>
      </div>
      <div class="card card-pad">
        <h2 class="font-semibold mb-3">Per-paper Silver scores</h2>
        <VChart v-if="silver.length" :option="heatOption" autoresize :style="{ height: `${Math.max(260, paperIds.length * 16 + 120)}px` }" />
        <div v-else class="text-ink-400 text-sm">No silver scores yet.</div>
      </div>
      <div class="card card-pad xl:col-span-2 overflow-x-auto">
        <h2 class="font-semibold mb-3">Papers ({{ paperIds.length }} scored)</h2>
        <table class="table">
          <thead><tr><th>#</th><th>paper</th><th>miners</th><th>avg</th><th>best</th><th>my score</th><th>pdf</th></tr></thead>
          <tbody>
            <tr v-for="(p, i) in perPaperBest" :key="p.id">
              <td class="tabular-nums">{{ i + 1 }}</td>
              <td class="max-w-lg"><div>{{ p.title }}</div><div class="mono text-[11px] text-ink-400">{{ p.id }}</div></td>
              <td class="tabular-nums">{{ p.n }}</td><td class="tabular-nums">{{ fmtNum(p.avg, 3) }}</td>
              <td class="tabular-nums">{{ p.best ? `${fmtNum(p.best.score, 3)} (uid ${p.best.uid})` : '—' }}</td>
              <td><span v-if="p.mine" class="pill" :class="p.mine.score > 0.6 ? 'pill-ok' : 'pill-bad'">{{ fmtNum(p.mine.score, 3) }}</span><span v-else class="text-ink-500">—</span></td>
              <td><a v-if="papers.find((x: any) => x.paper_id === p.id)?.source_url" :href="papers.find((x: any) => x.paper_id === p.id).source_url" target="_blank" class="text-accent-300 hover:underline text-xs">pdf</a></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
