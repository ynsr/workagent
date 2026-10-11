
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

/**
 * ISO-8601 stamp → mainstream relative label ("just now", "5 min ago",
 * "3 hours ago", "Yesterday", "3 days ago", "2 weeks ago", "3 months ago",
 * "1 year ago"); missing/unparseable → "—". Future stamps clamp to
 * "just now". Older than ~1y falls back to the locale date.
 */
export function relativeDate(iso: string | null | undefined): string {
  if (!iso) return "—"
  const ms = Date.parse(iso)
  if (Number.isNaN(ms)) return "—"
  const diff = Date.now() - ms
  if (diff < 10_000) return "just now"
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return "just now"
  if (mins < 60) return mins === 1 ? "1 min ago" : `${mins} min ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return hours === 1 ? "1 hour ago" : `${hours} hours ago`
  const days = Math.floor(hours / 24)
  if (days === 1) return "Yesterday"
  if (days < 7) return `${days} days ago`
  const weeks = Math.floor(days / 7)
  if (weeks < 5) return weeks === 1 ? "1 week ago" : `${weeks} weeks ago`
  const months = Math.floor(days / 30)
  if (months < 12) return months <= 1 ? "1 month ago" : `${months} months ago`
  const years = Math.floor(days / 365)
  if (years < 2) return "1 year ago"
  return new Date(ms).toLocaleDateString()
}

/** ISO-8601 stamp → exact local date-time for tooltips; "" when unparseable. */
export function exactDateTime(iso: string | null | undefined): string {
  if (!iso) return ""
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString()
}

export function shortId(id: string): string {
  return id.length > 10 ? id.slice(0, 10) : id
}

/**
 * Display form of a worktree path: strips the default worktree root prefix
 * (`$HOME/dev/worktrees/`) so the Path column stays readable. The frontend
 * does not know `$HOME`, so anything up to and including `/dev/worktrees/`
 * is stripped; paths outside that root (custom scan_root) are shown in full.
 */
export function shortWorktreePath(path: string | null | undefined): string {
  if (!path) return ""
  const m = path.match(/^.*?\/dev\/worktrees\/(.+)$/)
  return m?.[1] ? m[1] as string : path
}

export function argsText(args: string[] | undefined): string {
  if (!args || args.length === 0) return ""
  return args.join(" ")
}

/** Shell command that cds to the worktree then continues the session.
 * Real transcripts resume via omp --resume; anything else (missing/empty
 * preview path — the frontend cannot stat it) starts fresh scoped by
 * --session-dir on the parent dir (omp v18+ rejects --resume there).
 * Callers with a known-good transcript pass resume=true. */
export function resumeCommand(worktree: string, sessionFile: string, resume = false): string {
  const q = (s: string) => `'${s.replaceAll("'", "'\\''")}'`
  if (resume) return `cd ${q(worktree)} && omp --resume ${q(sessionFile)}`
  const dir = sessionFile.includes("/") ? sessionFile.slice(0, sessionFile.lastIndexOf("/")) || "/" : "."
  return `cd ${q(worktree)} && omp --session-dir ${q(dir)}`
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
