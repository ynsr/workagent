
/** "just now" / "42s ago" / "3m ago" / "2h ago" / "5d ago" / absolute date. */
export function relativeTime(epochSeconds: number): string {
  const ms = epochSeconds * 1000
  const diff = Date.now() - ms
  if (!Number.isFinite(diff)) return String(epochSeconds)
  if (diff < 10_000) return "just now"
  const s = Math.floor(diff / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  if (d < 7) return `${d}d ago`
  return new Date(ms).toLocaleString()
}

export function shortId(id: string): string {
  return id.length > 10 ? id.slice(0, 10) : id
}

export function argsText(args: string[] | undefined): string {
  if (!args || args.length === 0) return ""
  return args.join(" ")
}

export async function copyToClipboard(text: string): Promise<void> {
  if (!navigator.clipboard) {
    throw new Error("Clipboard is not available")
  }
  await navigator.clipboard.writeText(text)
}

function csvCell(value: string): string {
  if (/[",\n]/.test(value)) return `"${value.replaceAll('"', '""')}"`
  return value
}

export function toCsv(headers: string[], rows: string[][]): string {
  const head = headers.map(csvCell).join(",")
  const body = rows.map((r) => r.map((c) => csvCell(c ?? "")).join(","))
  return [head, ...body].join("\n")
}

export function downloadText(filename: string, text: string, mime = "text/plain"): void {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
