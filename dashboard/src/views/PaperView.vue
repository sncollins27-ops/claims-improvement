<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { ArrowLeft, RefreshCw } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import Kpi from '../components/Kpi.vue'
import ClaimInspector from '../components/ClaimInspector.vue'
import { api, fmtNum, fmtUsd, fmtSeconds, qualityClass } from '../api'
import { VChart, baseAxis, tooltipStyle, palette } from '../lib/echarts'

const props = defineProps<{ run: string; paper: string }>()
const artifact = ref<any>(null)
const evaluation = ref<any>(null)
const trace = ref<any>(null)
const source = ref<any>(null)
const manifest = ref<any>(null)
const summary = ref<any>(null)
const log = ref('')
const loading = ref(true)
const tab = ref<'claims' | 'findings' | 'pipeline' | 'structure' | 'raw' | 'log'>('claims')
const selectedClaim = ref<string | null>(null)
const claimFilter = ref('')
const importanceFilter = ref('all')
const verdictFilter = ref('all')
const rawWhich = ref<'artifact' | 'evaluation' | 'manifest' | 'trace'>('artifact')

async function load() {
  loading.value = true
  const get = async (name: string) => { try { return await api.paperFile(props.run, props.paper, name) } catch { return null } }
  const [a, e, t, s, m, sm, l] = await Promise.all([
    get('artifact'), get('evaluation'), get('trace'), get('source'), get('manifest'), api.paperSummary(props.run, props.paper).catch(() => null), api.paperLog(props.run, props.paper).catch(() => ({ text: '' })),
  ])
  artifact.value = a; evaluation.value = e; trace.value = t; source.value = s; manifest.value = m; summary.value = sm; log.value = l?.text ?? ''
  loading.value = false
  if (!selectedClaim.value && a?.logic?.claims?.length) selectedClaim.value = a.logic.claims[0]?.claim_id ?? null
}
onMounted(load)
watch(() => [props.run, props.paper], load)

const claims = computed<any[]>(() => artifact.value?.logic?.claims ?? [])
const evidenceById = computed<Record<string, any>>(() => Object.fromEntries((artifact.value?.evidence?.records ?? []).map((r: any) => [r.evidence_id, r])))
const experimentsById = computed<Record<string, any>>(() => Object.fromEntries((artifact.value?.logic?.experiments ?? []).map((r: any) => [r.experiment_id, r])))
const spansById = computed<Record<string, any>>(() => Object.fromEntries((source.value?.spans ?? []).map((s: any) => [s.span_id, s])))
const judgeResults = computed<Record<string, any>>(() => evaluation.value?.judge?.results ?? {})
const findings = computed<any[]>(() => evaluation.value?.findings ?? [])
const findingsByTarget = computed<Record<string, any[]>>(() => {
  const map: Record<string, any[]> = {}
  for (const f of findings.value) { const k = f.target_id ?? '_'; (map[k] ??= []).push(f) }
  return map
})
const filteredClaims = computed(() => claims.value.filter((c) => {
  const imp = c.metadata?.importance ?? 'unknown'
  const verdict = judgeResults.value[c.claim_id]?.verdict ?? 'n/a'
  if (importanceFilter.value !== 'all' && imp !== importanceFilter.value) return false
  if (verdictFilter.value !== 'all' && verdict !== verdictFilter.value) return false
  if (claimFilter.value && !`${c.claim_id} ${c.statement} ${c.conditions}`.toLowerCase().includes(claimFilter.value.toLowerCase())) return false
  return true
}))
const selected = computed(() => claims.value.find((c) => c.claim_id === selectedClaim.value) ?? null)
const stats = computed(() => evaluation.value?.stats ?? {})
const det = computed(() => evaluation.value?.deterministic ?? {})
const severityOrder = ['blocker', 'critical', 'major', 'minor', 'warning', 'suggestion']
const findingsBySeverity = computed(() => severityOrder.map((s) => ({ severity: s, rows: findings.value.filter((f) => f.severity === s) })).filter((g) => g.rows.length))
const codeCounts = computed(() => {
  const counts: Record<string, number> = {}
  for (const f of findings.value) { const c = f.metadata?.code ?? f.dimension; counts[c] = (counts[c] ?? 0) + 1 }
  return Object.entries(counts).sort((a, b) => b[1] - a[1])
})

const spanChartOption = computed(() => {
  const rows: any[] = trace.value?.spans ?? []
  const names = rows.map((r) => `p${r.page ?? '?'}`)
  const raw = rows.map((r) => (r.parts ?? []).reduce((a: number, p: any) => a + (p.raw_candidates ?? 0), 0))
  const kept = rows.map((r) => (r.parts ?? []).reduce((a: number, p: any) => a + (p.kept ?? 0), 0))
  const cited = rows.map((r) => claims.value.filter((c) => (c.sources ?? []).some((s: any) => (s.span_ids ?? []).includes(r.span_id))).length)
  return {
    tooltip: { trigger: 'axis', ...tooltipStyle },
    legend: { data: ['raw candidates', 'grounded', 'final claims citing'], textStyle: { color: '#8b98b8' }, top: 0 },
    grid: { left: 40, right: 20, top: 34, bottom: 30 },
    xAxis: baseAxis({ type: 'category', data: names }),
    yAxis: baseAxis({ type: 'value' }),
    series: [
      { name: 'raw candidates', type: 'bar', data: raw, itemStyle: { color: palette[3], borderRadius: [4, 4, 0, 0] } },
      { name: 'grounded', type: 'bar', data: kept, itemStyle: { color: palette[0], borderRadius: [4, 4, 0, 0] } },
      { name: 'final claims citing', type: 'line', data: cited, itemStyle: { color: palette[1] }, smooth: true },
    ],
  }
})
const stageRows = computed(() => trace.value?.stages ?? [])
const candidatesById = computed<Record<string, any>>(() => Object.fromEntries((trace.value?.candidates ?? []).map((c: any) => [c.id, c])))
const mergeRows = computed(() => claims.value
  .map((c) => ({ claim: c, members: (c.metadata?.candidate_ids ?? []).map((id: string) => candidatesById.value[id]).filter(Boolean) }))
  .filter((r) => r.members.length > 1)
  .sort((a, b) => b.members.length - a.members.length))
const droppedCandidateIds = computed(() => new Set((trace.value?.consolidation?.dropped ?? []).map((d: any) => d.candidate_id)))
const usedCandidateIds = computed(() => new Set(claims.value.flatMap((c) => c.metadata?.candidate_ids ?? [])))
const lostCandidates = computed(() => (trace.value?.candidates ?? []).filter((c: any) => !usedCandidateIds.value.has(c.id) && !droppedCandidateIds.value.has(c.id)))
const usageRows = computed(() => {
  const out: any[] = []
  for (const [model, u] of Object.entries<any>(trace.value?.usage ?? {})) {
    for (const [stage, s] of Object.entries<any>(u.by_stage ?? {})) out.push({ model: u.model ?? model, stage, ...s })
  }
  return out
})
const rawText = computed(() => JSON.stringify({ artifact: artifact.value, evaluation: evaluation.value, manifest: manifest.value, trace: trace.value }[rawWhich.value] ?? {}, null, 2))
function verdictPill(id: string) {
  const v = judgeResults.value[id]?.verdict
  return v === 'accept' ? 'pill-ok' : v === 'weak' ? 'pill-warn' : v === 'reject' ? 'pill-bad' : 'pill-muted'
}
</script>

<template>
  <div>
    <PageHeader :title="summary?.title ?? paper" :subtitle="`${run} · ${paper}`">
      <RouterLink class="btn" :to="`/runs?run=${encodeURIComponent(run)}`"><ArrowLeft class="h-4 w-4" /> runs</RouterLink>
      <button class="btn" @click="load"><RefreshCw class="h-4 w-4" /></button>
    </PageHeader>

    <section class="px-8 grid grid-cols-3 xl:grid-cols-8 gap-3">
      <Kpi label="claims" :value="stats.claims ?? claims.length" :tone="(stats.claims ?? claims.length) >= 40 ? 'ok' : 'warn'" />
      <Kpi label="evidence" :value="stats.evidence_records ?? '—'" />
      <Kpi label="experiments" :value="stats.experiments ?? '—'" />
      <Kpi label="span coverage" :value="stats.span_coverage != null ? `${Math.round(stats.span_coverage * 100)}%` : '—'" :hint="`${stats.spans_cited ?? '?'} / ${stats.spans_total ?? '?'} pages cited`" />
      <Kpi label="diag. quality" :value="det.diagnostic_quality_estimate != null ? fmtNum(det.diagnostic_quality_estimate, 2) : '—'" :tone="(det.diagnostic_quality_estimate ?? 0) >= 0.9 ? 'ok' : 'warn'" :hint="`${findings.length} findings`" />
      <Kpi label="judge" :value="evaluation?.judge ? `${evaluation.judge.accepted_count}✓ ${evaluation.judge.weak_count}~ ${evaluation.judge.rejected_count}✗` : '—'" :tone="(evaluation?.judge?.rejected_count ?? 0) === 0 ? 'ok' : 'bad'" :hint="evaluation?.judge?.model" />
      <Kpi label="cost" :value="fmtUsd(summary?.cost_usd)" :hint="`${summary?.tokens ?? '—'} tokens`" />
      <Kpi label="time" :value="fmtSeconds(summary?.wall_seconds)" :hint="summary?.runtime" />
    </section>

    <nav class="px-8 mt-6 flex gap-1 border-b border-ink-800">
      <button v-for="t in ['claims', 'findings', 'pipeline', 'structure', 'raw', 'log']" :key="t" class="px-4 py-2 text-sm capitalize border-b-2 -mb-px transition-colors" :class="tab === t ? 'border-accent-400 text-accent-300' : 'border-transparent text-ink-400 hover:text-ink-200'" @click="tab = t as any">{{ t }}</button>
    </nav>

    <section v-if="tab === 'claims'" class="px-8 py-5 grid grid-cols-1 xl:grid-cols-5 gap-4">
      <div class="xl:col-span-2 card overflow-hidden flex flex-col max-h-[78vh]">
        <div class="p-3 flex gap-2 border-b border-ink-800">
          <input v-model="claimFilter" class="input flex-1" placeholder="search claims" />
          <select v-model="importanceFilter" class="input"><option value="all">all importance</option><option value="central">central</option><option value="supporting">supporting</option><option value="minor">minor</option></select>
          <select v-model="verdictFilter" class="input"><option value="all">all verdicts</option><option value="accept">accept</option><option value="weak">weak</option><option value="reject">reject</option></select>
        </div>
        <div class="overflow-y-auto">
          <button v-for="c in filteredClaims" :key="c.claim_id" class="w-full text-left px-4 py-3 border-b border-ink-800/70 hover:bg-ink-800/60 transition-colors" :class="selectedClaim === c.claim_id ? 'bg-accent-500/10' : ''" @click="selectedClaim = c.claim_id">
            <div class="flex items-center gap-2 mb-1">
              <span class="mono text-xs text-ink-300">{{ c.claim_id }}</span>
              <span class="pill" :class="c.metadata?.importance === 'central' ? 'pill-info' : 'pill-muted'">{{ c.metadata?.importance ?? '—' }}</span>
              <span class="pill pill-muted">{{ c.metadata?.claim_type ?? c.status }}</span>
              <span v-if="judgeResults[c.claim_id]" class="pill" :class="verdictPill(c.claim_id)">{{ judgeResults[c.claim_id].verdict }}</span>
              <span v-if="findingsByTarget[c.claim_id]?.length" class="pill pill-warn">{{ findingsByTarget[c.claim_id]?.length }} finding(s)</span>
            </div>
            <div class="text-[13px] text-ink-100 leading-snug">{{ c.statement }}</div>
          </button>
          <div v-if="!filteredClaims.length" class="p-6 text-sm text-ink-400">No claims match.</div>
        </div>
      </div>
      <div class="xl:col-span-3">
        <ClaimInspector v-if="selected" :claim="selected" :evidence-by-id="evidenceById" :experiments-by-id="experimentsById" :spans-by-id="spansById" :judge="judgeResults[selected.claim_id]" :findings="findingsByTarget[selected.claim_id] ?? []" />
        <div v-else class="card card-pad text-ink-400">Select a claim.</div>
      </div>
    </section>

    <section v-else-if="tab === 'findings'" class="px-8 py-5 space-y-4">
      <div class="card card-pad">
        <div class="flex flex-wrap gap-2">
          <span v-for="[code, n] in codeCounts" :key="code" class="pill pill-muted mono">{{ code }} × {{ n }}</span>
          <span v-if="!codeCounts.length" class="text-mint-400 text-sm">No deterministic or local findings. 🎯</span>
        </div>
      </div>
      <div v-for="group in findingsBySeverity" :key="group.severity" class="card card-pad">
        <h3 class="font-semibold capitalize mb-2 flex items-center gap-2"><span class="pill" :class="['blocker', 'critical'].includes(group.severity) ? 'pill-bad' : group.severity === 'major' ? 'pill-warn' : 'pill-muted'">{{ group.severity }}</span> {{ group.rows.length }}</h3>
        <table class="table">
          <thead><tr><th>id</th><th>pass</th><th>dimension</th><th>target</th><th>message</th><th>code</th></tr></thead>
          <tbody>
            <tr v-for="f in group.rows" :key="f.finding_id">
              <td class="mono text-xs">{{ f.finding_id }}</td><td>{{ f.pass_name }}</td><td>{{ f.dimension }}</td>
              <td class="mono text-xs"><button class="text-accent-300 hover:underline" @click="selectedClaim = f.target_id; tab = 'claims'">{{ f.target_type }} {{ f.target_id }}</button></td>
              <td>{{ f.message }}<div v-if="f.metadata?.quote" class="quote mt-1">{{ f.metadata.quote }}</div></td>
              <td class="mono text-xs">{{ f.metadata?.code }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-else-if="tab === 'pipeline'" class="px-8 py-5 grid grid-cols-1 xl:grid-cols-3 gap-4">
      <div class="card card-pad xl:col-span-2">
        <h3 class="font-semibold mb-2">Per-page extraction funnel</h3>
        <VChart v-if="trace" :option="spanChartOption" autoresize style="height: 260px" />
        <div v-else class="text-ink-400 text-sm">No staged_trace.json (not a staged run).</div>
        <table v-if="trace" class="table mt-3">
          <thead><tr><th>page</th><th>chars</th><th>raw</th><th>kept</th><th>quote methods</th><th>repair</th><th>dropped (reason)</th></tr></thead>
          <tbody>
            <tr v-for="s in trace.spans" :key="s.span_id">
              <td class="mono text-xs">{{ s.span_id }}</td><td class="tabular-nums">{{ s.chars }}</td>
              <td class="tabular-nums">{{ (s.parts ?? []).reduce((a: number, p: any) => a + (p.raw_candidates ?? 0), 0) }}</td>
              <td class="tabular-nums font-semibold">{{ (s.parts ?? []).reduce((a: number, p: any) => a + (p.kept ?? 0), 0) }}</td>
              <td class="mono text-[11px]">{{ (s.parts ?? []).map((p: any) => p.quote_methods ? `e${p.quote_methods.exact}/n${p.quote_methods.normalized}/f${p.quote_methods.fuzzy}/x${p.quote_methods.failed}` : p.skipped ?? '').join(' ') }}</td>
              <td class="tabular-nums">{{ (s.parts ?? []).map((p: any) => p.repair_requested ? `${p.repaired}/${p.repair_requested}` : '').join(' ') }}</td>
              <td class="text-[11px] text-ink-300">
                <div v-for="(d, i) in (s.parts ?? []).flatMap((p: any) => p.dropped ?? []).slice(0, 4)" :key="i"><span class="text-rose-400">{{ d.reason }}</span> · {{ d.statement }}</div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="space-y-4">
        <div class="card card-pad">
          <h3 class="font-semibold mb-2">Stages</h3>
          <table class="table"><tbody>
            <tr v-for="s in stageRows" :key="s.key"><td class="mono text-xs">{{ s.key }}</td><td class="tabular-nums">{{ fmtSeconds(s.duration_seconds) }}</td><td class="text-[11px] text-ink-400">{{ Object.entries(s.metadata ?? {}).map(([k, v]) => `${k}=${v}`).join(' ') }}</td></tr>
          </tbody></table>
        </div>
        <div class="card card-pad">
          <h3 class="font-semibold mb-2">Usage by stage</h3>
          <table class="table"><thead><tr><th>stage</th><th>calls</th><th>in</th><th>out</th><th>reason</th><th>cost</th></tr></thead><tbody>
            <tr v-for="u in usageRows" :key="u.model + u.stage"><td class="mono text-xs">{{ u.stage }}</td><td class="tabular-nums">{{ u.requests }}</td><td class="tabular-nums">{{ u.prompt_tokens }}</td><td class="tabular-nums">{{ u.completion_tokens }}</td><td class="tabular-nums">{{ u.reasoning_tokens }}</td><td class="tabular-nums">{{ fmtUsd(u.cost_usd) }}</td></tr>
          </tbody></table>
        </div>
        <div v-if="trace?.consolidation?.dropped?.length" class="card card-pad">
          <h3 class="font-semibold mb-2">Dropped in consolidation ({{ trace.consolidation.dropped.length }})</h3>
          <div v-for="(d, i) in trace.consolidation.dropped" :key="i" class="text-[12px] text-ink-300 border-b border-ink-800 py-1"><span class="text-rose-400">{{ d.reason }}</span> — {{ d.statement }}</div>
        </div>
        <div v-if="mergeRows.length" class="card card-pad">
          <h3 class="font-semibold mb-2">Merged claims ({{ mergeRows.length }}) <span class="text-ink-400 text-xs font-normal">candidates folded into one claim; check that members really restate it</span></h3>
          <div v-for="r in mergeRows" :key="r.claim.claim_id" class="border-b border-ink-800 py-2 text-[12px]">
            <div class="flex gap-2 items-center"><button class="mono text-accent-300 hover:underline" @click="selectedClaim = r.claim.claim_id; tab = 'claims'">{{ r.claim.claim_id }}</button><span class="pill pill-muted">{{ r.members.length }} members</span><span class="text-ink-100">{{ r.claim.statement.slice(0, 140) }}</span></div>
            <div v-for="m in r.members" :key="m.id" class="ml-6 text-ink-400">↳ <span class="mono">{{ m.id }}</span> p{{ m.page }} · {{ m.statement.slice(0, 150) }}</div>
          </div>
        </div>
        <div v-if="lostCandidates.length" class="card card-pad">
          <h3 class="font-semibold mb-2 text-rose-400">Candidates lost without a reason ({{ lostCandidates.length }})</h3>
          <div v-for="c in lostCandidates" :key="c.id" class="text-[12px] text-ink-300 border-b border-ink-800 py-1"><span class="mono">{{ c.id }}</span> p{{ c.page }} · {{ c.statement }}</div>
        </div>
        <div v-if="trace?.warnings?.length" class="card card-pad">
          <h3 class="font-semibold mb-2 text-amber-400">Warnings</h3>
          <pre class="text-[11px] mono text-ink-300 whitespace-pre-wrap">{{ JSON.stringify(trace.warnings, null, 1) }}</pre>
        </div>
      </div>
    </section>

    <section v-else-if="tab === 'structure'" class="px-8 py-5 grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div class="card card-pad">
        <h3 class="font-semibold mb-2">Experiments ({{ artifact?.logic?.experiments?.length ?? 0 }})</h3>
        <div v-for="e in artifact?.logic?.experiments ?? []" :key="e.experiment_id" class="border-b border-ink-800 py-2">
          <div class="flex gap-2 items-center"><span class="mono text-xs text-ink-300">{{ e.experiment_id }}</span><span class="font-medium text-sm">{{ e.title }}</span><span class="pill pill-muted">{{ e.verifies.length }} claims</span></div>
          <div class="text-[12px] text-ink-300 mt-1"><span class="text-ink-400">setup:</span> {{ e.setup }}</div>
          <div class="text-[12px] text-ink-300"><span class="text-ink-400">procedure:</span> {{ e.procedure }}</div>
          <div class="text-[12px] text-ink-300"><span class="text-ink-400">expected:</span> {{ e.expected_outcome }}</div>
        </div>
      </div>
      <div class="space-y-4">
        <div class="card card-pad">
          <h3 class="font-semibold mb-2">Concepts ({{ artifact?.logic?.concepts?.length ?? 0 }})</h3>
          <div v-for="k in artifact?.logic?.concepts ?? []" :key="k.concept_id" class="text-[12px] py-1 border-b border-ink-800"><span class="font-medium text-ink-100">{{ k.label }}</span> — <span class="text-ink-300">{{ k.definition }}</span></div>
        </div>
        <div class="card card-pad">
          <h3 class="font-semibold mb-2">Paper framing</h3>
          <div class="text-[12px] space-y-2 text-ink-300">
            <div><span class="text-ink-400">key insight:</span> {{ artifact?.logic?.key_insight }}</div>
            <div><span class="text-ink-400">observations:</span><ul class="list-disc ml-5"><li v-for="(o, i) in artifact?.logic?.problem_observations ?? []" :key="i">{{ o }}</li></ul></div>
            <div><span class="text-ink-400">gaps:</span><ul class="list-disc ml-5"><li v-for="(o, i) in artifact?.logic?.gaps ?? []" :key="i">{{ o }}</li></ul></div>
            <div><span class="text-ink-400">constraints:</span><ul class="list-disc ml-5"><li v-for="(o, i) in artifact?.logic?.constraints ?? []" :key="i">{{ o }}</li></ul></div>
            <div><span class="text-ink-400">claims_summary:</span><ul class="list-disc ml-5"><li v-for="(o, i) in artifact?.paper?.claims_summary ?? []" :key="i">{{ o }}</li></ul></div>
          </div>
        </div>
        <div class="card card-pad">
          <h3 class="font-semibold mb-2">Trace tree</h3>
          <pre class="text-[11px] mono text-ink-300 whitespace-pre-wrap max-h-96 overflow-auto">{{ JSON.stringify(artifact?.trace ?? {}, (k, v) => (k === 'source_refs' ? undefined : v), 1) }}</pre>
        </div>
      </div>
    </section>

    <section v-else-if="tab === 'raw'" class="px-8 py-5">
      <div class="flex gap-2 mb-3">
        <button v-for="w in ['artifact', 'evaluation', 'manifest', 'trace']" :key="w" class="btn" :class="rawWhich === w ? 'btn-primary' : ''" @click="rawWhich = w as any">{{ w }}</button>
      </div>
      <pre class="card card-pad text-[11px] mono text-ink-200 whitespace-pre-wrap max-h-[75vh] overflow-auto">{{ rawText }}</pre>
    </section>

    <section v-else class="px-8 py-5">
      <pre class="card card-pad text-[11px] mono text-ink-300 whitespace-pre-wrap max-h-[75vh] overflow-auto">{{ log || 'no log' }}</pre>
    </section>
  </div>
</template>
