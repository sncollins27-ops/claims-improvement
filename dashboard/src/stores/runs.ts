import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api, type RunRow } from '../api'

export const useRunsStore = defineStore('runs', () => {
  const runs = ref<RunRow[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const loadedAt = ref<number | null>(null)

  async function refresh() {
    loading.value = true
    error.value = null
    try {
      runs.value = (await api.runs()).runs
      loadedAt.value = Date.now()
    } catch (e: any) {
      error.value = String(e?.message ?? e)
    } finally {
      loading.value = false
    }
  }

  const experiments = computed(() => runs.value.filter((r) => r.kind === 'experiment'))
  const neuronRuns = computed(() => runs.value.filter((r) => r.kind === 'neuron'))
  const byName = computed(() => Object.fromEntries(runs.value.map((r) => [r.name, r])))

  return { runs, loading, error, loadedAt, refresh, experiments, neuronRuns, byName }
})
