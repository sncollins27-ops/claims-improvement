export function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

function normalizeForSearch(text: string): { norm: string; map: number[] } {
  const map: number[] = []
  let norm = ''
  let prevSpace = true
  for (let i = 0; i < text.length; i++) {
    const ch = text.charAt(i)
    if (/\s/.test(ch)) {
      if (prevSpace) continue
      norm += ' '
      map.push(i)
      prevSpace = true
    } else {
      norm += ch.toLowerCase()
      map.push(i)
      prevSpace = false
    }
  }
  return { norm, map }
}

/** Return HTML of `text` with every `quotes` occurrence wrapped in <mark>. Whitespace-insensitive. */
export function highlightQuotes(text: string, quotes: string[]): string {
  if (!text) return ''
  const { norm, map } = normalizeForSearch(text)
  const ranges: Array<[number, number]> = []
  for (const quote of quotes) {
    if (!quote) continue
    const q = normalizeForSearch(quote).norm
    if (q.length < 8) continue
    let from = 0
    while (from < norm.length) {
      const idx = norm.indexOf(q, from)
      if (idx < 0) break
      const start = map[idx] as number
      const end = (map[idx + q.length - 1] as number) + 1
      ranges.push([start, end])
      from = idx + q.length
    }
  }
  ranges.sort((a, b) => a[0] - b[0])
  const merged: Array<[number, number]> = []
  for (const r of ranges) {
    const last = merged[merged.length - 1]
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1])
    else merged.push([r[0], r[1]])
  }
  let html = ''
  let cursor = 0
  for (const [s, e] of merged) {
    html += escapeHtml(text.slice(cursor, s)) + '<mark>' + escapeHtml(text.slice(s, e)) + '</mark>'
    cursor = e
  }
  html += escapeHtml(text.slice(cursor))
  return html
}

export function tokens(text: string): Set<string> {
  return new Set((text || '').toLowerCase().replace(/[^a-z0-9\s]+/g, ' ').split(/\s+/).filter((t) => t.length > 2))
}

export function jaccard(a: string, b: string): number {
  const ta = tokens(a)
  const tb = tokens(b)
  if (!ta.size || !tb.size) return 0
  let inter = 0
  for (const t of ta) if (tb.has(t)) inter++
  return inter / (ta.size + tb.size - inter)
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
