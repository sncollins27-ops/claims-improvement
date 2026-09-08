<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import PageHeader from '../components/PageHeader.vue'
import { useRunsStore } from '../stores/runs'
import { api, fmtNum, fmtUsd, fmtSeconds, qualityClass } from '../api'
import { jaccard } from '../lib/text'

const store = useRunsStore()
const left = ref('')
const right = ref('')
const paper = ref('')
const leftArtifact = ref<any>(null)
const rightArtifact = ref<any>(null)
const leftEval = ref<any>(null)
const rightEval = ref<any>(null)
const threshold = ref(0.5)

onMounted(async () => {
  await store.refresh()
  if (store.runs.length >= 2) { left.value = store.runs[1]?.name ?? ''; right.value = store.runs[0]?.name ?? '' }
  else if (store.runs.length === 1) { left.value = right.value = store.runs[0]?.name ?? '' }
})
const leftRun = computed(() => store.byName[left.value])
const rightRun = computed(() => store.byName[right.value])
const commonPapers = computed(() => {
  const l = new Set((leftRun.value?.papers ?? []).map((p) => p.paper_id))
  return (rightRun.value?.papers ?? []).filter((p) => l.has(p.paper_id)).map((p) => p.paper_id)
})
watch(commonPapers, (v) => { if (!paper.value || !v.includes(paper.value)) paper.value = v[0] ?? '' })
watch([left, right, paper], async () => {
  leftArtifact.value = rightArtifact.value = leftEval.value = rightEval.value = null
  if (!paper.value || !left.value || !right.value) return
  const get = async (run: string, name: string) => { try { return await api.paperFile(run, paper.value, name) } catch { return null } }
  ;[leftArtifact.value, rightArtifact.value, leftEval.value, rightEval.value] = await Promise.all([get(left.value, 'artifact'), get(right.value, 'artifact'), get(left.value, 'evaluation'), get(right.value, 'evaluation')])
})

function paperRow(run: any, id: string) { return run?.papers?.find((p: any) => p.paper_id === id) }
const perPaper = computed(() => commonPapers.value.map((id) => ({ id, l: paperRow(leftRun.value, id), r: paperRow(rightRun.value, id) })))

interface Match { claim: any; best: any | null; score: number }
const leftClaims = computed<any[]>(() => leftArtifact.value?.logic?.claims ?? [])
const rightClaims = computed<any[]>(() => rightArtifact.value?.logic?.claims ?? [])
function matches(from: any[], to: any[]): Match[] {
  return from.map((claim) => {
    let best: any = null; let score = 0
    for (const other of to) { const s = jaccard(claim.statement, other.statement); if (s > score) { score = s; best = other } }
    return { claim, best: score >= threshold.value ? best : null, score }
  })
}
const leftMatches = computed(() => matches(leftClaims.value, rightClaims.value))
const rightMatches = computed(() => matches(rightClaims.value, leftClaims.value))
const leftCovered = computed(() => leftMatches.value.filter((m) => m.best).length)
const rightCovered = computed(() => rightMatches.value.filter((m) => m.best).length)
function judgeOf(ev: any, id: string) { return ev?.judge?.results?.[id]?.verdict }
</script>

<template>
  <div>
    <PageHeader title="Compare" subtitle="Two experiments on the same paper: coverage overlap (token Jaccard), quality, cost and time.">
      <select v-model="left" class="input"><option v-for="r in store.runs" :key="r.name" :value="r.name">{{ r.name }}</option></select>
      <span class="text-ink-400">vs</span>
      <select v-model="right" class="input"><option v-for="r in store.runs" :key="r.name" :value="r.name">{{ r.name }}</option></select>
    </PageHeader>

    <section class="px-8">
      <div class="card overflow-x-auto">
        <table class="table">
          <thead><tr><th>paper</th><th>claims</th><th>evidence</th><th>diag</th><th>judge ✓/~/✗</th><th>quality</th><th>cost</th><th>time</th></tr></thead>
          <tbody>
            <tr v-for="row in perPaper" :key="row.id" class="cursor-pointer" :class="paper === row.id ? 'bg-accent-500/10' : ''" @click="paper = row.id">
              <td class="max-w-md"><div class="text-ink-100">{{ row.r?.title ?? row.id }}</div><div class="text-[11px] mono text-ink-400">{{ row.id }}</div></td>
              <td class="tabular-nums">{{ row.l?.stats?.claims ?? '—' }} → <b>{{ row.r?.stats?.claims ?? '—' }}</b></td>
              <td class="tabular-nums">{{ row.l?.stats?.evidence_records ?? '—' }} → {{ row.r?.stats?.evidence_records ?? '—' }}</td>
              <td><span class="pill" :class="qualityClass(row.l?.diagnostic_quality_estimate)">{{ fmtNum(row.l?.diagnostic_quality_estimate, 2) }}</span> → <span class="pill" :class="qualityClass(row.r?.diagnostic_quality_estimate)">{{ fmtNum(row.r?.diagnostic_quality_estimate, 2) }}</span></td>
              <td class="mono text-xs">{{ row.l?.judge ? `${row.l.judge.accepted_count}/${row.l.judge.weak_count}/${row.l.judge.rejected_count}` : '—' }} → {{ row.r?.judge ? `${row.r.judge.accepted_count}/${row.r.judge.weak_count}/${row.r.judge.rejected_count}` : '—' }}</td>
              <td><span class="pill" :class="qualityClass(row.l?.quality_estimate)">{{ fmtNum(row.l?.quality_estimate, 3) }}</span> → <span class="pill" :class="qualityClass(row.r?.quality_estimate)">{{ fmtNum(row.r?.quality_estimate, 3) }}</span></td>
              <td class="tabular-nums">{{ fmtUsd(row.l?.cost_usd) }} → {{ fmtUsd(row.r?.cost_usd) }}</td>
              <td class="tabular-nums">{{ fmtSeconds(row.l?.wall_seconds) }} → {{ fmtSeconds(row.r?.wall_seconds) }}</td>
            </tr>
            <tr v-if="!perPaper.length"><td colspan="8" class="text-ink-400">No paper appears in both runs.</td></tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="paper" class="px-8 py-5">
      <div class="flex items-center gap-3 mb-3 text-sm text-ink-300">
        <span>claim overlap threshold</span>
        <input v-model.number="threshold" type="range" min="0.2" max="0.9" step="0.05" class="w-40" /><span class="mono">{{ threshold }}</span>
        <span class="pill pill-muted">{{ left }}: {{ leftCovered }}/{{ leftClaims.length }} matched in {{ right }}</span>
        <span class="pill pill-muted">{{ right }}: {{ rightCovered }}/{{ rightClaims.length }} matched in {{ left }}</span>
      </div>
      <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div class="card card-pad max-h-[70vh] overflow-y-auto">
          <h3 class="font-semibold mb-2 mono text-sm">{{ left }} <span class="text-ink-400">({{ leftClaims.length }} claims)</span></h3>
          <div v-for="m in leftMatches" :key="m.claim.claim_id" class="py-2 border-b border-ink-800 text-[13px]">
            <div class="flex gap-2 items-center mb-1"><span class="mono text-xs text-ink-400">{{ m.claim.claim_id }}</span><span class="pill" :class="m.best ? 'pill-ok' : 'pill-warn'">{{ m.best ? `matched ${m.best.claim_id} (${m.score.toFixed(2)})` : 'only here' }}</span><span v-if="judgeOf(leftEval, m.claim.claim_id)" class="pill pill-muted">{{ judgeOf(leftEval, m.claim.claim_id) }}</span></div>
            <div class="text-ink-200">{{ m.claim.statement }}</div>
          </div>
        </div>
        <div class="card card-pad max-h-[70vh] overflow-y-auto">
          <h3 class="font-semibold mb-2 mono text-sm">{{ right }} <span class="text-ink-400">({{ rightClaims.length }} claims)</span></h3>
          <div v-for="m in rightMatches" :key="m.claim.claim_id" class="py-2 border-b border-ink-800 text-[13px]">
            <div class="flex gap-2 items-center mb-1"><span class="mono text-xs text-ink-400">{{ m.claim.claim_id }}</span><span class="pill" :class="m.best ? 'pill-ok' : 'pill-info'">{{ m.best ? `matched ${m.best.claim_id} (${m.score.toFixed(2)})` : 'new here' }}</span><span v-if="judgeOf(rightEval, m.claim.claim_id)" class="pill pill-muted">{{ judgeOf(rightEval, m.claim.claim_id) }}</span></div>
            <div class="text-ink-200">{{ m.claim.statement }}</div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>
