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
