import { useEffect, useMemo, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { parseAcpBlocks, type AcpBlock } from "@/lib/harness"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import type { RunLine } from "@/lib/api"

const COLLAPSED_TOOL_STATUSES = new Set(["completed", "done", "success", "ok"])

function isToolExpanded(status?: string): boolean {
  const s = (status ?? "").trim().toLowerCase()
  return !COLLAPSED_TOOL_STATUSES.has(s)
}

function toolIcon(kind?: string): string {
  switch ((kind ?? "").trim().toLowerCase()) {
    case "read":
      return "📖"
    case "edit":
    case "write":
      return "✏️"
    case "execute":
    case "bash":
      return "⚡"
    case "search":
      return "🔍"
    default:
      return "⚙"
  }
}

function formatArgs(args: unknown): string {
  if (args == null) return ""
  if (typeof args === "string") return args
  if (typeof args === "object" && Object.keys(args as Record<string, unknown>).length === 0) return ""
  try {
    return JSON.stringify(args, null, 2)
  } catch {
    return String(args)
  }
}

function BlockView({ block }: { block: AcpBlock }) {
  switch (block.kind) {
    case "thought":
      return (
        <details className="rounded border border-white/10 bg-white/[0.03] px-2.5 py-1.5">
          <summary className="cursor-pointer select-none text-xs text-white/50">Thinking</summary>
          <div className="mt-1.5 whitespace-pre-wrap text-xs italic text-white/55">{block.text}</div>
        </details>
      )
    case "tool_call": {
      const argsText = formatArgs(block.args)
      const outText = block.output || block.text || ""
      return (
        <details
          open={isToolExpanded(block.status)}
          className="rounded border border-white/10 bg-white/5 px-2.5 py-1.5"
        >
          <summary className="flex cursor-pointer select-none items-center gap-2 text-xs text-sky-300">
            <span>
              {toolIcon(block.toolKind)} {block.name || block.toolKind || "tool"}
            </span>
            {block.status ? <Badge>{block.status}</Badge> : null}
          </summary>
          <div className="mt-1.5 space-y-1.5 font-mono text-xs text-white/80">
            {argsText ? (
              <pre className="overflow-x-auto whitespace-pre-wrap rounded bg-black/30 p-2 text-[11px] text-white/70">
                {argsText}
              </pre>
            ) : null}
            {outText ? <div className="whitespace-pre-wrap break-words">{outText}</div> : null}
          </div>
        </details>
      )
    }
    case "usage":
      return <div className="text-xs text-white/40">{block.text}</div>
    case "question":
      return (
        <div className="rounded border border-sky-500/40 bg-sky-500/10 px-2.5 py-1.5" data-testid="acp-question">
          <span className="text-sky-300">❓ {block.text}</span>
        </div>
      )
    case "turn_end":
      return <div className="text-xs text-white/30">— turn end —</div>
    case "message":
      return (
        <div className="prose prose-sm prose-invert max-w-none break-words font-sans text-sm leading-relaxed">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.text}</ReactMarkdown>
        </div>
      )
    default:
      return <div className="whitespace-pre-wrap break-words">{block.text || " "}</div>
  }
}

/** Structured ACP transcript (issues #112, #128): collapsible blocks, Markdown, rAF autoscroll. */
export function AcpTranscript({ lines }: { lines: RunLine[] }) {
  const blocks = useMemo(() => parseAcpBlocks(lines), [lines])
  const structured = blocks.filter((b) => b.kind !== "raw").length
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const programmaticRef = useRef(false)
  const [follow, setFollow] = useState(true)

  useEffect(() => {
    if (!follow) return
    const el = scrollRef.current
    if (!el) return
    const rafId = window.requestAnimationFrame(() => {
      programmaticRef.current = true
      el.scrollTop = el.scrollHeight
      window.requestAnimationFrame(() => {
        programmaticRef.current = false
      })
    })
    return () => window.cancelAnimationFrame(rafId)
  }, [blocks, follow])

  function handleScroll() {
    if (programmaticRef.current) return
    const el = scrollRef.current
    if (!el) return
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    if (distance > 48) setFollow(false)
    else if (distance <= 16 && !follow) setFollow(true)
  }

  if (structured === 0) return null
  return (
    <div className="mb-4 rounded-lg border" data-testid="acp-transcript">
      <div className="flex items-center justify-between border-b border-white/10 px-3 py-2 text-xs text-white/70">
        <span>
          ACP transcript — {structured} structured block{structured === 1 ? "" : "s"} ({blocks.length} total)
        </span>
        <label className="flex items-center gap-2 text-white/80">
          <Checkbox
            checked={follow}
            onCheckedChange={(v) => setFollow(v === true)}
            aria-label="Follow ACP transcript"
          />
          Follow output
        </label>
      </div>
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex max-h-[60vh] flex-col gap-2 overflow-auto px-3 py-2.5 font-mono text-[12.5px] leading-relaxed"
      >
        {blocks.map((b, i) => (
          <BlockView key={b.id ? `${b.kind}-${b.id}` : i} block={b} />
        ))}
      </div>
    </div>
  )
}
