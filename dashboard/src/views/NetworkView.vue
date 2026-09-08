<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { RefreshCw } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import Kpi from '../components/Kpi.vue'
import { api, fmtNum, fmtTime, ago } from '../api'

const data = ref<any>(null)
const loading = ref(false)
const network = ref('mainnet')
async function load() {
  loading.value = true
  try { data.value = await api.networkOverview(network.value) } finally { loading.value = false }
}
onMounted(load)
const runs = computed<any[]>(() => data.value?.runs ?? [])
const leaderboard = computed<any[]>(() => {
  const lb = data.value?.leaderboard
  const rows = Array.isArray(lb) ? lb : lb?.items ?? lb?.miners ?? lb?.leaderboard ?? []
  return rows.slice(0, 20)
})
const me = computed(() => data.value?.me)
const myRuns = computed(() => runs.value.filter((r) => (r.target_uids ?? []).includes(data.value?.my_uid)))
function lbValue(row: any, keys: string[]) { for (const k of keys) if (row[k] !== undefined && row[k] !== null) return row[k]; return null }
</script>

<template>
  <div>
    <PageHeader title="Network" subtitle="Live SN111 data from api.claims111.ai (cached by the sidecar).">
      <select v-model="network" class="input" @change="load"><option value="mainnet">mainnet</option><option value="testnet">testnet</option></select>
      <button class="btn" @click="load"><RefreshCw class="h-4 w-4" /> Refresh</button>
    </PageHeader>

    <section class="px-8 grid grid-cols-2 xl:grid-cols-5 gap-4">
      <Kpi label="My UID" :value="data?.my_uid ?? '—'" :hint="data?.my_hotkey ? data.my_hotkey.slice(0, 10) + '…' : ''" tone="info" />
      <Kpi label="Latest score" :value="me ? fmtNum(me.latest_score, 3) : '—'" :hint="me?.reports?.[0]?.run_id" :tone="(me?.latest_score ?? 0) > 0.6 ? 'ok' : 'bad'" />
      <Kpi label="Best / avg" :value="me ? `${fmtNum(me.best_score, 3)} / ${fmtNum(me.average_score, 3)}` : '—'" :hint="`${me?.report_count ?? 0} evaluations`" />
      <Kpi label="Selected in" :value="`${myRuns.length} of ${runs.length}`" hint="recent runs" />
      <Kpi label="Latest top score" :value="runs[0] ? fmtNum(runs[0].silver_batch_top_score, 3) : '—'" :hint="runs[0] ? `${runs[0].run_display_id} · ${ago(runs[0].ended_at)}` : ''" />
    </section>

    <section class="px-8 mt-6 grid grid-cols-1 xl:grid-cols-3 gap-4 pb-10">
      <div class="card card-pad xl:col-span-2 overflow-x-auto">
        <h2 class="font-semibold mb-3">Recent runs</h2>
        <table class="table">
          <thead><tr><th>run</th><th>status</th><th>started</th><th>ended</th><th>papers</th><th>resp</th><th>avg</th><th>median</th><th>top</th><th>scores</th><th>me</th></tr></thead>
          <tbody>
            <tr v-for="r in runs" :key="r.run_id">
              <td><RouterLink :to="`/network/runs/${r.run_id}`" class="text-accent-300 hover:underline mono text-xs">{{ r.run_display_id ?? r.run_id }}</RouterLink></td>
              <td><span class="pill" :class="r.status === 'completed' ? 'pill-ok' : r.status === 'failed' ? 'pill-bad' : 'pill-warn'">{{ r.status }}</span></td>
              <td class="text-ink-400 text-xs">{{ fmtTime(r.started_at) }}</td><td class="text-ink-400 text-xs">{{ fmtTime(r.ended_at) }}</td>
              <td class="tabular-nums">{{ r.paper_count }}</td><td class="tabular-nums">{{ r.response_count }}/{{ (r.target_uids ?? []).length }}</td>
              <td class="tabular-nums">{{ fmtNum(r.silver_average_score, 3) }}</td><td class="tabular-nums">{{ fmtNum(r.silver_median_score, 3) }}</td><td class="tabular-nums font-semibold">{{ fmtNum(r.silver_batch_top_score, 3) }}</td>
              <td class="tabular-nums">{{ r.silver_score_count }}</td>
              <td><span v-if="(r.target_uids ?? []).includes(data?.my_uid)" class="pill pill-info">selected</span></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="space-y-4">
        <div class="card card-pad">
          <h2 class="font-semibold mb-3">Leaderboard (7d)</h2>
          <table class="table">
            <thead><tr><th>#</th><th>uid</th><th>score</th><th>runs</th></tr></thead>
            <tbody>
              <tr v-for="(row, i) in leaderboard" :key="i" :class="lbValue(row, ['uid']) === data?.my_uid ? 'bg-accent-500/10' : ''">
                <td class="tabular-nums">{{ lbValue(row, ['rank']) ?? i + 1 }}</td>
                <td class="mono text-xs">{{ lbValue(row, ['uid']) }}</td>
                <td class="tabular-nums">{{ fmtNum(lbValue(row, ['score', 'average_score', 'mean_score', 'latest_score']), 3) }}</td>
                <td class="tabular-nums">{{ lbValue(row, ['run_count', 'report_count', 'evaluations']) ?? '—' }}</td>
              </tr>
              <tr v-if="!leaderboard.length"><td colspan="4" class="text-ink-400">no leaderboard data</td></tr>
            </tbody>
          </table>
        </div>
        <div v-if="me?.reports?.length" class="card card-pad">
          <h2 class="font-semibold mb-3">My reports</h2>
          <div v-for="r in me.reports" :key="r.run_id" class="border-b border-ink-800 py-2 text-[12px]">
            <div class="flex items-center gap-2"><RouterLink :to="`/network/runs/${r.run_id}`" class="mono text-accent-300 hover:underline">{{ r.run_id }}</RouterLink><span class="pill" :class="r.score > 0.6 ? 'pill-ok' : r.score > 0 ? 'pill-warn' : 'pill-bad'">{{ fmtNum(r.score, 3) }}</span><span class="text-ink-400">{{ ago(r.created_at) }}</span></div>
            <div class="text-ink-400 mt-1">{{ (r.paper_scores ?? []).filter((p: any) => p.status === 'completed').length }} completed · {{ (r.paper_scores ?? []).filter((p: any) => p.status === 'failed').length }} failed of {{ (r.paper_scores ?? []).length }}</div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>
