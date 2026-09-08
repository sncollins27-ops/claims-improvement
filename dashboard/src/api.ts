export interface PaperSummary {
  run_name: string
  paper_id: string
  title: string
  status: string
  error?: string | null
  runtime?: string | null
  model?: string | null
  models?: Record<string, string> | null
  note?: string | null
  started_at?: string | null
  ended_at?: string | null
  wall_seconds?: number | null
  cost_usd?: number | null
  tokens?: number | null
  stats: Record<string, any>
  quality_estimate?: number | null
  diagnostic_quality_estimate?: number | null
  adjudication_quality_estimate?: number | null
  severity_summary?: Record<string, number> | null
  judge?: { verdicts?: Record<string, number>; rejected_count?: number; weak_count?: number; accepted_count?: number; model?: string } | null
  has_trace: boolean
  has_evaluation: boolean
  artifact_bytes: number
  updated_at: number
}

export interface RunRow {
  name: string
  kind: 'experiment' | 'neuron'
  papers: PaperSummary[]
  paper_count: number
  completed: number
  failed: number
  avg_claims: number | null
  avg_quality: number | null
  total_cost_usd: number | null
  runtime?: string | null
  model?: string | null
  updated_at: number
}

export interface AgendaItem {
  id: string
  title: string
  detail?: string
  status: 'todo' | 'doing' | 'done' | 'blocked'
  priority: 'p0' | 'p1' | 'p2'
  tag?: string
  created_at?: string
  updated_at?: string
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json() as Promise<T>
}

export const api = {
  runs: () => getJson<{ runs: RunRow[] }>('/api/runs'),
  paperFile: <T = any>(run: string, paper: string, name: string) => getJson<T>(`/api/runs/${run}/paper/${paper}/file/${name}`),
  paperSummary: (run: string, paper: string) => getJson<PaperSummary>(`/api/runs/${run}/paper/${paper}/summary`),
  paperLog: (run: string, paper: string) => getJson<{ text: string }>(`/api/runs/${run}/paper/${paper}/log`),
  networkOverview: (network = 'mainnet') => getJson<any>(`/api/network/overview?network=${network}`),
  networkRun: (runId: string, network = 'mainnet') => getJson<any>(`/api/network/runs/${runId}?network=${network}`),
  networkMiner: (uid: number, network = 'mainnet') => getJson<any>(`/api/network/miners/${uid}?network=${network}`),
  networkFeedback: (uid: number, runId?: string, network = 'mainnet') => getJson<any[]>(`/api/network/miners/${uid}/silver-feedback?network=${network}${runId ? `&run_id=${runId}` : ''}`),
  status: () => getJson<any>('/api/status'),
  skill: () => getJson<any>('/api/skill'),
  agenda: () => getJson<{ items: AgendaItem[]; updated_at: string | null }>('/api/agenda'),
  saveAgenda: async (items: AgendaItem[]) => {
    const res = await fetch('/api/agenda', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ items }) })
    if (!res.ok) throw new Error(await res.text())
    return res.json() as Promise<{ items: AgendaItem[]; updated_at: string }>
  },
  handoff: () => getJson<{ prompt: string }>('/api/handoff'),
}

export function fmtNum(value: unknown, digits = 2): string {
  if (value === null || value === undefined || value === '' || Number.isNaN(Number(value))) return '—'
  return Number(value).toFixed(digits)
}
export function fmtInt(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return Number(value).toLocaleString()
}
export function fmtUsd(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return `$${Number(value).toFixed(3)}`
}
export function fmtSeconds(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  const s = Number(value)
  if (s < 90) return `${s.toFixed(0)}s`
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`
}
export function fmtTime(value: string | number | null | undefined): string {
  if (!value) return '—'
  const d = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  if (Number.isNaN(d.getTime())) return String(value)
  return d.toISOString().replace('T', ' ').slice(0, 16) + 'Z'
}
export function ago(value: string | number | null | undefined): string {
  if (!value) return '—'
  const d = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  const diff = (Date.now() - d.getTime()) / 1000
  if (diff < 60) return `${Math.round(diff)}s ago`
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`
  if (diff < 86400) return `${(diff / 3600).toFixed(1)}h ago`
  return `${(diff / 86400).toFixed(1)}d ago`
}
export function qualityClass(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'pill-muted'
  if (value >= 0.9) return 'pill-ok'
  if (value >= 0.7) return 'pill-warn'
  return 'pill-bad'
}
