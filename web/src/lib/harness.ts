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
  /** Present for tool_call blocks (name/kind/status/args/output rendering). */
  name?: string
  toolKind?: string
  status?: string
  /** Present for question blocks. */
  qid?: string
  options?: string[]
  multi?: boolean
}

const ACP_PREFIX = "ACP:"

/** Parse run log lines into structured ACP blocks.
 * Non-ACP lines fall back to raw blocks; a malformed first line (1 MiB
 * head-truncation) is skipped gracefully instead of breaking the parse. */
export function parseAcpBlocks(lines: { seq: number; text: string }[]): AcpBlock[] {
  const out: AcpBlock[] = []
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
      case "thought":
        out.push({ kind: "thought", text: String(obj.text ?? "") })
        break
      case "tool_call":
        out.push({
          kind: "tool_call",
          text: String(obj.output ?? obj.args ?? obj.text ?? ""),
          name: typeof obj.name === "string" ? obj.name : undefined,
          toolKind: typeof obj.kind === "string" ? obj.kind : undefined,
          status: typeof obj.status === "string" ? obj.status : undefined,
        })
        break
      case "usage":
        out.push({ kind: "usage", text: String(obj.text ?? JSON.stringify(obj)) })
        break
      case "question":
        out.push({
          kind: "question",
          text: String(obj.text ?? ""),
          qid: typeof obj.qid === "string" ? obj.qid : undefined,
          options: Array.isArray(obj.options) ? obj.options.map(String) : undefined,
          multi: obj.multi === true,
        })
        break
      case "turn_end":
        out.push({ kind: "turn_end", text: "" })
        break
      default:
        out.push({ kind: "message", text: String(obj.text ?? payload) })
        break
    }
  }
  return out
}
