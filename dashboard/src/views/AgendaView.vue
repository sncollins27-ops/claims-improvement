<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Copy, Plus, Trash2 } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import { api, type AgendaItem } from '../api'
import { copyText } from '../lib/text'

const items = ref<AgendaItem[]>([])
const updatedAt = ref<string | null>(null)
const handoff = ref('')
const copied = ref(false)
const saving = ref(false)
const draft = ref({ title: '', detail: '', priority: 'p1' as AgendaItem['priority'], tag: '' })

async function load() {
  const [a, h] = await Promise.all([api.agenda(), api.handoff()])
  items.value = a.items
  updatedAt.value = a.updated_at
  handoff.value = h.prompt
}
onMounted(load)
async function save() {
  saving.value = true
  try { const r = await api.saveAgenda(items.value); updatedAt.value = r.updated_at; handoff.value = (await api.handoff()).prompt } finally { saving.value = false }
}
function add() {
  if (!draft.value.title.trim()) return
  items.value.unshift({ id: `a${Date.now()}`, title: draft.value.title.trim(), detail: draft.value.detail.trim(), priority: draft.value.priority, tag: draft.value.tag.trim() || undefined, status: 'todo', created_at: new Date().toISOString() })
  draft.value = { title: '', detail: '', priority: 'p1', tag: '' }
  save()
}
function cycle(item: AgendaItem) {
  const order: AgendaItem['status'][] = ['todo', 'doing', 'done', 'blocked']
  item.status = order[(order.indexOf(item.status) + 1) % order.length] ?? 'todo'
  item.updated_at = new Date().toISOString()
  save()
}
function remove(item: AgendaItem) { items.value = items.value.filter((i) => i.id !== item.id); save() }
const grouped = computed(() => (['doing', 'todo', 'blocked', 'done'] as const).map((s) => ({ status: s, rows: items.value.filter((i) => i.status === s).sort((a, b) => a.priority.localeCompare(b.priority)) })))
const statusClass: Record<string, string> = { todo: 'pill-muted', doing: 'pill-info', done: 'pill-ok', blocked: 'pill-bad' }
async function copyHandoff() { copied.value = await copyText(handoff.value); setTimeout(() => (copied.value = false), 1500) }
</script>

<template>
  <div>
    <PageHeader title="Agenda" :subtitle="`What to do next. Persisted in server/data/agenda.json${updatedAt ? ' · saved ' + updatedAt : ''}`" />
    <section class="px-8 pb-10 grid grid-cols-1 xl:grid-cols-3 gap-4">
      <div class="xl:col-span-2 space-y-4">
        <div class="card card-pad flex flex-wrap gap-2 items-end">
          <div class="flex-1 min-w-64"><div class="kpi-label mb-1">title</div><input v-model="draft.title" class="input w-full" placeholder="e.g. Reduce ungrounded numbers on methods pages" @keyup.enter="add" /></div>
          <div class="flex-1 min-w-64"><div class="kpi-label mb-1">detail</div><input v-model="draft.detail" class="input w-full" placeholder="why / how" @keyup.enter="add" /></div>
          <div><div class="kpi-label mb-1">priority</div><select v-model="draft.priority" class="input"><option>p0</option><option>p1</option><option>p2</option></select></div>
          <div><div class="kpi-label mb-1">tag</div><input v-model="draft.tag" class="input w-28" placeholder="skill / runtime" /></div>
          <button class="btn btn-primary" @click="add"><Plus class="h-4 w-4" /> add</button>
        </div>
        <div v-for="g in grouped" :key="g.status" class="card card-pad">
          <h3 class="font-semibold capitalize mb-2 flex items-center gap-2"><span class="pill" :class="statusClass[g.status]">{{ g.status }}</span><span class="text-ink-400 text-sm">{{ g.rows.length }}</span></h3>
          <div v-for="item in g.rows" :key="item.id" class="flex gap-3 items-start py-2 border-b border-ink-800/70 last:border-0">
            <button class="pill mt-0.5" :class="statusClass[item.status]" title="click to advance status" @click="cycle(item)">{{ item.status }}</button>
            <span class="pill mt-0.5" :class="item.priority === 'p0' ? 'pill-bad' : item.priority === 'p1' ? 'pill-warn' : 'pill-muted'">{{ item.priority }}</span>
            <div class="flex-1 min-w-0">
              <div class="text-sm text-ink-100" :class="item.status === 'done' ? 'line-through text-ink-400' : ''">{{ item.title }}</div>
              <div v-if="item.detail" class="text-[12px] text-ink-400">{{ item.detail }}</div>
              <div v-if="item.tag" class="text-[10px] text-ink-500 mono">{{ item.tag }}</div>
            </div>
            <button class="text-ink-500 hover:text-rose-400" @click="remove(item)"><Trash2 class="h-4 w-4" /></button>
          </div>
          <div v-if="!g.rows.length" class="text-sm text-ink-500">—</div>
        </div>
      </div>
      <div class="card card-pad h-fit">
        <div class="flex items-center justify-between mb-2">
          <h3 class="font-semibold">Next Claude session handoff</h3>
          <button class="btn" @click="copyHandoff"><Copy class="h-4 w-4" /> {{ copied ? 'copied' : 'copy' }}</button>
        </div>
        <p class="text-[12px] text-ink-400 mb-2">Paste this as the first message of the next session. It carries the repo paths, the loop, latest runs and open items.</p>
        <pre class="text-[11px] mono text-ink-200 whitespace-pre-wrap max-h-[70vh] overflow-auto">{{ handoff }}</pre>
      </div>
    </section>
  </div>
</template>
