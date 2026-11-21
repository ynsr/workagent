/** Shared ACP harness helpers (issue #112): model/effort flags + ACP log parsing. */

export interface ModelEffort {
  model: string
  effort: string
}

/** `--model`/`--effort` argv (each emitted only when non-empty). */
export function modelEffortArgs(f: Partial<ModelEffort>): string[] {
  return modelEffortFlags(f.model ?? "", f.effort ?? "")
}

export function modelEffortFlags(model: string, effort: string): string[] {
  return [
    ...(model.trim() ? ["--model", model.trim()] : []),
    ...(effort.trim() ? ["--effort", effort.trim()] : []),
  ]
}

export type AcpBlockKind = "message" | "thought" | "tool_call" | "usage" | "question" | "turn_end" | "raw"

export interface AcpBlock {
  kind: AcpBlockKind
  text: string
  id?: string
  name?: string
  toolKind?: string
  status?: string
  args?: unknown
  output?: string
  qid?: string
  options?: string[]
  multi?: boolean
}

function formatUsage(obj: Record<string, unknown>): string {
  if (typeof obj.text === "string" && obj.text.trim()) return obj.text.trim()
  const u = (typeof obj.usage === "object" && obj.usage !== null
    ? obj.usage
    : obj) as Record<string, unknown>
  const parts: string[] = []
  const pick = (keys: string[], label: string) => {
    for (const k of keys) {
      if (typeof u[k] === "number") {
        parts.push(`${label} ${u[k]}`)
        return
      }
    }
  }
  pick(["inputTokens", "input_tokens", "promptTokens", "input"], "in")
  pick(["outputTokens", "output_tokens", "completionTokens", "output"], "out")
  pick(["totalTokens", "total_tokens", "total"], "total")
  if (parts.length > 0) return `tokens: ${parts.join(" · ")}`
  const fallback = Object.entries(u)
    .filter(([k]) => k !== "t" && k !== "id")
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
  return fallback.length > 0 ? fallback.join(" · ") : "usage"
}

function normalizeOptions(raw: unknown): string[] | undefined {
  if (!Array.isArray(raw)) return undefined
  return raw.map((o) =>
    typeof o === "object" && o !== null && "label" in o
      ? String((o as Record<string, unknown>).label ?? "")
      : String(o ?? ""),
  )
}

export function hasAcpStructuredEvents(lines: { seq: number; text: string }[]): boolean {
  return parseAcpBlocks(lines).some((b) => b.kind !== "raw")
}

const ACP_PREFIX = "ACP:"

/** Parse run log lines into structured ACP blocks.
 * Non-ACP lines fall back to raw blocks; a malformed first line (1 MiB
 * head-truncation) is skipped gracefully instead of breaking the parse. */
export function parseAcpBlocks(lines: { seq: number; text: string }[]): AcpBlock[] {
  const out: AcpBlock[] = []
  const toolIndex = new Map<string, number>()
  const isNonEmpty = (v: unknown): boolean => {
    if (v === undefined || v === null) return false
    if (typeof v === "string") return v !== ""
    if (Array.isArray(v)) return v.length > 0
    if (typeof v === "object") return Object.keys(v as Record<string, unknown>).length > 0
    return true
  }
  const formatOutput = (v: unknown): string => {
    if (typeof v === "string") return v
    if (Array.isArray(v)) return v.length > 0 ? JSON.stringify(v) : ""
    if (typeof v === "object" && v !== null) {
      return Object.keys(v as Record<string, unknown>).length > 0 ? JSON.stringify(v) : ""
    }
    if (v === undefined || v === null) return ""
    return String(v)
  }
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i]?.text ?? ""
    const idx = raw.indexOf(ACP_PREFIX)
    if (idx < 0) {
      out.push({ kind: "raw", text: raw })
      continue
    }
    const payload = raw.slice(idx + ACP_PREFIX.length).trim()
    let obj: Record<string, unknown>
    try {
      obj = JSON.parse(payload) as Record<string, unknown>
    } catch {
      // Head-truncated first line (or any malformed JSON) → skip only when
      // it is the first line, else keep as raw so nothing is hidden.
      if (i === 0) continue
      out.push({ kind: "raw", text: raw })
      continue
    }
    const t = typeof obj.t === "string" ? obj.t : "message"
    switch (t) {
      case "thought": {
        const text = String(obj.text ?? "")
        const last = out[out.length - 1]
        if (last?.kind === "thought") {
          last.text += text
        } else {
          out.push({ kind: "thought", text })
        }
        break
      }
      case "message": {
        const text = String(obj.text ?? "")
        const last = out[out.length - 1]
        if (last?.kind === "message") {
          last.text += text
        } else {
          out.push({ kind: "message", text })
        }
        break
      }
      case "tool_call": {
        const id = typeof obj.id === "string" ? obj.id : ""
        const output = formatOutput(obj.output)
        if (id && toolIndex.has(id)) {
          const existing = out[toolIndex.get(id) as number] as AcpBlock
          if (typeof obj.name === "string") existing.name = obj.name
          if (typeof obj.kind === "string") existing.toolKind = obj.kind
          if (typeof obj.status === "string") existing.status = obj.status
          if (isNonEmpty(obj.args)) existing.args = obj.args
          if (output !== "") {
            existing.output = output
            existing.text = output
          }
        } else {
          const block: AcpBlock = {
            kind: "tool_call",
            text: output,
            name: typeof obj.name === "string" ? obj.name : undefined,
            toolKind: typeof obj.kind === "string" ? obj.kind : undefined,
            status: typeof obj.status === "string" ? obj.status : undefined,
          }
          if (id) block.id = id
          if (isNonEmpty(obj.args)) block.args = obj.args
          if (output !== "") block.output = output
          out.push(block)
          if (id) toolIndex.set(id, out.length - 1)
        }
        break
      }
      case "usage":
        out.push({ kind: "usage", text: formatUsage(obj) })
        break
      case "question":
        out.push({
          kind: "question",
          text: String(obj.text ?? ""),
          qid: typeof obj.qid === "string" ? obj.qid : undefined,
          options: normalizeOptions(obj.options),
          multi: obj.multi === true,
        })
        break
      case "turn_end":
        out.push({ kind: "turn_end", text: "" })
        break
      default: {
        // Unknown future event type: own block, never merged into a
        // preceding message (hides the event boundary otherwise).
        const text = String(obj.text ?? payload)
        out.push({ kind: "message", text })
        break
      }
    }
  }
  return out
}

export interface ActivityRunCounts {
  reads: number
  searches: number
  commands: number
  edits: number
}

export interface ActivityRunGroup {
  kind: "activity_run"
  id: string
  counts: ActivityRunCounts
  calls: AcpBlock[]
  failedCalls: AcpBlock[]
}

export type AcpTranscriptItem = AcpBlock | ActivityRunGroup

const FAILED_TOOL_STATUSES = new Set(["failed", "error", "errored", "cancelled", "canceled", "timeout"])

export function isFailedToolCall(block: AcpBlock): boolean {
  if (block.kind !== "tool_call") return false
  return FAILED_TOOL_STATUSES.has((block.status ?? "").trim().toLowerCase())
}

const READ_NAMES = new Set(["read", "read_file", "view_file", "cat"])
const SEARCH_NAMES = new Set(["search", "grep", "grep_search", "find", "find_by_name", "glob", "ripgrep", "rg", "list_dir"])
const EXEC_NAMES = new Set(["execute", "bash", "run_command", "shell"])
const EDIT_NAMES = new Set(["edit", "write", "edit_file", "write_file", "replace_file_content", "multi_replace_file_content"])

const MUTATING_CMD_RE =
  /(?:^|[;&|]\s*)(?:rm|mv|cp|mkdir|rmdir|touch|chmod|chown|git\s+(?:commit|push|add|reset|checkout|rebase|merge|cherry-pick|stash|apply)|npm\s+(?:install|i|ci|uninstall)|pip\s+install|uv\s+(?:add|remove|sync)|sed\s+-i|tee\b)/i
const REDIRECT_CMD_RE = /[^0-9]>>?\s*[^\s&]/

function isMutatingCommand(cmd: string): boolean {
  if (MUTATING_CMD_RE.test(cmd)) return true
  const unquoted = cmd.replace(/'[^']*'|"[^"]*"/g, '""')
  return REDIRECT_CMD_RE.test(unquoted)
}

function extractCommandString(args: unknown): string {
  if (typeof args === "string") return args
  if (typeof args === "object" && args !== null) {
    const rec = args as Record<string, unknown>
    for (const k of ["command", "cmd", "CommandLine", "script"]) {
      if (typeof rec[k] === "string") return rec[k] as string
    }
  }
  return ""
}

type ExploratoryCategory = "reads" | "searches" | "commands" | null

function classifyExploratoryTool(block: AcpBlock): ExploratoryCategory {
  if (block.kind !== "tool_call") return null
  const kind = (block.toolKind ?? "").trim().toLowerCase()
  const name = (block.name ?? "").trim().toLowerCase()
  if (kind === "edit" || kind === "write" || EDIT_NAMES.has(name)) return null
  if (kind === "read" || READ_NAMES.has(name)) return "reads"
  if (kind === "search" || SEARCH_NAMES.has(name)) return "searches"
  if (kind === "execute" || kind === "bash" || EXEC_NAMES.has(name)) {
    const cmd = extractCommandString(block.args)
    if (cmd && isMutatingCommand(cmd)) return null
    return "commands"
  }
  return null
}

/** Cluster 3+ consecutive exploratory tool_call blocks (read, search,
 * non-mutating execute/bash) into a composite activity_run group. */
export function groupActivityRuns(blocks: AcpBlock[]): AcpTranscriptItem[] {
  const out: AcpTranscriptItem[] = []
  let buf: { block: AcpBlock; cat: Exclude<ExploratoryCategory, null>; startIdx: number }[] = []

  const flush = () => {
    if (buf.length === 0) return
    if (buf.length >= 3) {
      const counts: ActivityRunCounts = { reads: 0, searches: 0, commands: 0, edits: 0 }
      const calls: AcpBlock[] = []
      const failedCalls: AcpBlock[] = []
      for (const entry of buf) {
        counts[entry.cat] += 1
        calls.push(entry.block)
        if (isFailedToolCall(entry.block)) failedCalls.push(entry.block)
      }
      const first = buf[0]!
      out.push({
        kind: "activity_run",
        id: `activity-${first.block.id ?? first.startIdx}`,
        counts,
        calls,
        failedCalls,
      })
    } else {
      for (const entry of buf) out.push(entry.block)
    }
    buf = []
  }

  for (let i = 0; i < blocks.length; i++) {
    const block = blocks[i]!
    const cat = classifyExploratoryTool(block)
    if (cat) {
      buf.push({ block, cat, startIdx: i })
    } else {
      flush()
      out.push(block)
    }
  }
  flush()
  return out
}

export function formatActivityRunSummary(counts: ActivityRunCounts): string {
  const parts: string[] = []
  if (counts.reads > 0) parts.push(`${counts.reads} ${counts.reads === 1 ? "file" : "files"}`)
  if (counts.searches > 0) parts.push(`${counts.searches} ${counts.searches === 1 ? "search" : "searches"}`)
  if (counts.commands > 0) parts.push(`${counts.commands} ${counts.commands === 1 ? "command" : "commands"}`)
  if (counts.edits > 0) parts.push(`${counts.edits} ${counts.edits === 1 ? "edit" : "edits"}`)
  return parts.length > 0 ? `Explored ${parts.join(", ")}` : "Explored activity"
}

export function extractModelEffort(args?: string[]): { model?: string; effort?: string } {
  if (!args || args.length === 0) return {}
  let model: string | undefined
  let effort: string | undefined
  for (let i = 0; i < args.length; i++) {
    const a = args[i] ?? ""
    if (a === "--model" && i + 1 < args.length) {
      model = (args[++i] ?? "").trim() || undefined
    } else if (a.startsWith("--model=")) {
      model = a.slice("--model=".length).trim() || undefined
    } else if (a === "--effort" && i + 1 < args.length) {
      effort = (args[++i] ?? "").trim() || undefined
    } else if (a.startsWith("--effort=")) {
      effort = a.slice("--effort=".length).trim() || undefined
    }
  }
  return { model, effort }
}

export function formatTurnDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return ""
  const total = Math.round(seconds)
  if (total < 1) return "<1s"
  if (total < 60) return `${total}s`
  const mins = Math.floor(total / 60)
  const remSec = total % 60
  if (mins < 60) return remSec > 0 ? `${mins}m ${remSec}s` : `${mins}m`
  const hours = Math.floor(mins / 60)
  const remMin = mins % 60
  return remMin > 0 ? `${hours}h ${remMin}m` : `${hours}h`
}
