<script setup lang="ts">
import { computed, ref } from 'vue'
import { highlightQuotes } from '../lib/text'

const props = defineProps<{
  claim: any
  evidenceById: Record<string, any>
  experimentsById: Record<string, any>
  spansById: Record<string, any>
  judge?: any
  findings: any[]
}>()

const openSpan = ref<string | null>(null)
const sources = computed<any[]>(() => props.claim?.sources ?? [])
const evidence = computed<any[]>(() => (props.claim?.evidence_ids ?? []).map((id: string) => props.evidenceById[id]).filter(Boolean))
const experiments = computed<any[]>(() => (props.claim?.proof ?? []).map((id: string) => props.experimentsById[id]).filter(Boolean))
const quotesBySpan = computed<Record<string, string[]>>(() => {
  const map: Record<string, string[]> = {}
  const push = (ref: any) => { for (const id of ref.span_ids ?? []) { (map[id] ??= []); if (ref.quote) map[id].push(ref.quote) } }
  sources.value.forEach(push)
  evidence.value.forEach((e) => (e.source_refs ?? []).forEach(push))
  return map
})
function spanHtml(spanId: string) {
  const span = props.spansById[spanId]
  if (!span) return '<em>span not in source payload</em>'
  return highlightQuotes(span.text ?? '', quotesBySpan.value[spanId] ?? [])
}
const verdictClass = computed(() => {
  const v = props.judge?.verdict
  return v === 'accept' ? 'pill-ok' : v === 'weak' ? 'pill-warn' : v === 'reject' ? 'pill-bad' : 'pill-muted'
})
</script>

<template>
  <div class="card card-pad space-y-4 max-h-[78vh] overflow-y-auto">
    <div>
      <div class="flex items-center gap-2 flex-wrap mb-2">
        <span class="mono text-sm text-ink-300">{{ claim.claim_id }}</span>
        <span class="pill pill-info">{{ claim.metadata?.importance ?? '—' }}</span>
        <span class="pill pill-muted">{{ claim.metadata?.claim_type ?? '—' }}</span>
        <span class="pill pill-muted">{{ claim.status }}</span>
        <span v-if="claim.metadata?.topic" class="pill pill-muted">{{ claim.metadata.topic }}</span>
        <span v-if="claim.metadata?.pages?.length" class="pill pill-muted">p. {{ claim.metadata.pages.join(', ') }}</span>
        <span v-if="judge" class="pill" :class="verdictClass">judge: {{ judge.verdict }}<span v-if="judge.importance"> · {{ judge.importance }}</span></span>
      </div>
      <p class="text-[15px] leading-relaxed text-ink-100">{{ claim.statement }}</p>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-[13px]">
      <div><div class="kpi-label mb-1">conditions</div><div class="text-ink-200">{{ claim.conditions }}</div></div>
      <div><div class="kpi-label mb-1">falsification</div><div class="text-ink-200">{{ claim.falsification_criteria }}</div></div>
    </div>

    <div v-if="judge && (judge.issues?.length || judge.fix)" class="rounded-xl border border-amber-400/30 bg-amber-400/5 p-3 text-[13px]">
      <div class="kpi-label mb-1 text-amber-400">judge notes · {{ judge.evidence_status }}</div>
      <ul class="list-disc ml-5 text-ink-200"><li v-for="(i, k) in judge.issues" :key="k">{{ i }}</li></ul>
      <div v-if="judge.fix" class="text-ink-300 mt-1"><span class="text-ink-400">fix:</span> {{ judge.fix }}</div>
    </div>

    <div v-if="findings.length" class="rounded-xl border border-rose-400/30 bg-rose-400/5 p-3 text-[13px]">
      <div class="kpi-label mb-1 text-rose-400">findings</div>
      <div v-for="f in findings" :key="f.finding_id" class="text-ink-200"><span class="pill pill-muted mono mr-1">{{ f.severity }}</span>{{ f.message }}</div>
    </div>

    <div>
      <div class="kpi-label mb-2">sources ({{ sources.length }})</div>
      <div v-for="s in sources" :key="s.source_id" class="mb-2">
        <div class="flex items-center gap-2 text-[11px] text-ink-400 mb-1">
          <span class="mono">{{ s.source_id }}</span><span class="pill pill-muted">{{ s.role }}</span>
          <button v-for="id in s.span_ids" :key="id" class="mono text-accent-300 hover:underline" @click="openSpan = openSpan === id ? null : id">{{ id }}</button>
        </div>
        <div class="quote">{{ s.quote || '(no quote)' }}</div>
      </div>
    </div>

    <div v-if="evidence.length">
      <div class="kpi-label mb-2">evidence</div>
      <div v-for="e in evidence" :key="e.evidence_id" class="rounded-xl border border-ink-700 p-3 mb-2 text-[13px]">
        <div class="flex gap-2 items-center mb-1"><span class="mono text-xs text-ink-300">{{ e.evidence_id }}</span><span class="font-medium">{{ e.title }}</span><span class="pill pill-muted">{{ e.presentation_type }}</span></div>
        <div class="text-ink-200">{{ e.summary }}</div>
        <div class="text-[11px] text-ink-400 mt-1">method: {{ e.evidence_method }} · outcome: {{ e.outcome_type }} · {{ (e.source_refs ?? []).length }} refs</div>
      </div>
    </div>

    <div v-if="experiments.length">
      <div class="kpi-label mb-2">experiments (proof)</div>
      <div v-for="e in experiments" :key="e.experiment_id" class="text-[13px] mb-1"><span class="mono text-xs text-ink-300">{{ e.experiment_id }}</span> <span class="text-ink-100">{{ e.title }}</span> <span class="text-ink-400">— {{ e.expected_outcome }}</span></div>
    </div>

    <div v-if="openSpan">
      <div class="kpi-label mb-2">span {{ openSpan }} · page {{ spansById[openSpan]?.page }} · quotes highlighted</div>
      <div class="rounded-xl border border-ink-700 bg-ink-900 p-3 text-[12px] leading-relaxed text-ink-200 whitespace-pre-wrap max-h-96 overflow-auto" v-html="spanHtml(openSpan)"></div>
    </div>
  </div>
</template>
