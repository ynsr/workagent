import { useEffect, useMemo, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import {
  extractModelEffort,
  formatActivityRunSummary,
  formatTurnDuration,
  groupActivityRuns,
  parseAcpBlocks,
  type AcpBlock,
  type ActivityRunGroup,
} from "@/lib/harness"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import type { RunLine } from "@/lib/api"

export interface AcpRunMeta {
  created?: number
  finished?: number | null
  state?: string | null
  args?: string[]
  model?: string
  effort?: string
}

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

function toolKey(block: AcpBlock, index: number): string {
  return `${block.kind}-${block.id ?? index}`
}

function BlockView({ block, override, onToggle }: { block: AcpBlock; override?: boolean; onToggle: (open: boolean) => void }) {
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
      const open = override ?? isToolExpanded(block.status)
      return (
        <details
          open={open}
          onToggle={(e) => onToggle((e.target as HTMLDetailsElement).open)}
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
        <div className="max-w-none break-words font-sans text-sm leading-relaxed text-white/85 [&_a]:text-sky-300 [&_a]:underline [&_code]:rounded [&_code]:bg-black/30 [&_code]:px-1 [&_code]:font-mono [&_code]:text-[12.5px] [&_h1]:text-base [&_h1]:font-semibold [&_h2]:text-sm [&_h2]:font-semibold [&_h3]:text-sm [&_h3]:font-semibold [&_li]:ml-4 [&_li]:list-disc [&_p]:my-1 [&_pre]:overflow-x-auto [&_pre]:rounded [&_pre]:bg-black/30 [&_pre]:p-2 [&_strong]:font-semibold">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.text}</ReactMarkdown>
        </div>
      )
    default:
      return <div className="whitespace-pre-wrap break-words">{block.text || " "}</div>
  }
}

function ActivityRunView({
  group,
  overrides,
  onToggle,
}: {
  group: ActivityRunGroup
  overrides: Record<string, boolean>
  onToggle: (key: string, open: boolean) => void
}) {
  const open = overrides[group.id] ?? false
  const summary = formatActivityRunSummary(group.counts)
  return (
    <div className="space-y-1.5">
      <details
        data-testid="activity-run"
        open={open}
        onToggle={(e) => {
          if (e.target !== e.currentTarget) return
          onToggle(group.id, e.currentTarget.open)
        }}
        className="rounded border border-white/10 bg-white/[0.03] px-2.5 py-1.5"
      >
        <summary className="flex cursor-pointer select-none items-center gap-2 text-xs text-white/75">
          <span>
            {open ? "▾" : "▸"} {summary}
          </span>
          {group.failedCalls.length > 0 ? (
            <Badge variant="destructive">
              {group.failedCalls.length} failed
            </Badge>
          ) : null}
        </summary>
        <div className="mt-2 space-y-1.5 border-l border-white/10 pl-2.5">
          {group.calls.map((call, idx) => {
            const tkey = toolKey(call, idx)
            return (
              <BlockView
                key={`${group.id}-${tkey}`}
                block={call}
                override={overrides[tkey]}
                onToggle={(isOpen) => onToggle(tkey, isOpen)}
              />
            )
          })}
        </div>
      </details>
      {group.failedCalls.length > 0 ? (
        <div
          data-testid="failed-calls"
          className="ml-3 space-y-1.5 border-l-2 border-red-500/50 pl-2.5"
        >
          {group.failedCalls.map((call, idx) => {
            const tkey = toolKey(call, idx)
            return (
              <BlockView
                key={`failed-${group.id}-${tkey}`}
                block={call}
                override={overrides[tkey]}
                onToggle={(isOpen) => onToggle(tkey, isOpen)}
              />
            )
          })}
        </div>
      ) : null}
    </div>
  )
}

function buildTurnSummary(blocks: AcpBlock[], runMeta?: AcpRunMeta): string | null {
  const hasTurnEnd = blocks.some((b) => b.kind === "turn_end")
  const isTerminal = Boolean(runMeta?.state && runMeta.state !== "running")
  if (!hasTurnEnd && !isTerminal) return null

  const parts: string[] = []
  if (
    typeof runMeta?.created === "number" &&
    typeof runMeta?.finished === "number" &&
    runMeta.finished >= runMeta.created
  ) {
    const dur = formatTurnDuration(runMeta.finished - runMeta.created)
    if (dur) parts.push(`Worked for ${dur}`)
  }

  const fromArgs = extractModelEffort(runMeta?.args)
  const model = (runMeta?.model ?? fromArgs.model ?? "").trim()
  const effort = (runMeta?.effort ?? fromArgs.effort ?? "").trim()
  if (model && effort) parts.push(`${model} (${effort})`)
  else if (model) parts.push(model)
  else if (effort) parts.push(`(${effort})`)

  return parts.length > 0 ? parts.join(" · ") : null
}

/** Structured ACP transcript (issues #112, #128, #142): ActivityRun folding,
 * FailedCalls extraction, settled turn summaries, Markdown, rAF autoscroll. */
export function AcpTranscript({ lines, runMeta }: { lines: RunLine[]; runMeta?: AcpRunMeta }) {
  const blocks = useMemo(() => parseAcpBlocks(lines), [lines])
  const items = useMemo(() => groupActivityRuns(blocks), [blocks])
  const turnSummary = useMemo(() => buildTurnSummary(blocks, runMeta), [blocks, runMeta])
  const structured = blocks.filter((b) => b.kind !== "raw").length
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const programmaticRef = useRef(false)
  const [follow, setFollow] = useState(true)
  // Per-tool / per-group user open/close overrides (risk 4): keyed by stable
  // key, status default applies only to items the user never touched.
  const [overrides, setOverrides] = useState<Record<string, boolean>>({})
  const setOverride = (key: string, open: boolean) => {
    setOverrides((prev) => ({ ...prev, [key]: open }))
  }
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
      {turnSummary ? (
        <div
          data-testid="turn-summary-header"
          className="border-b border-white/10 bg-white/[0.02] px-3 py-1.5 text-xs text-white/60"
        >
          {turnSummary}
        </div>
      ) : null}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex max-h-[60vh] flex-col gap-2 overflow-auto px-3 py-2.5 font-mono text-[12.5px] leading-relaxed"
      >
        {items.map((item, i) => {
          if (item.kind === "activity_run") {
            return (
              <ActivityRunView
                key={item.id}
                group={item}
                overrides={overrides}
                onToggle={setOverride}
              />
            )
          }
          const key = item.id ? `${item.kind}-${item.id}` : i
          const tkey = toolKey(item, typeof key === "number" ? key : i)
          return (
            <BlockView
              key={key}
              block={item}
              override={item.kind === "tool_call" ? overrides[tkey] : undefined}
              onToggle={(open) => {
                if (item.kind !== "tool_call") return
                setOverride(tkey, open)
              }}
            />
          )
        })}
      </div>
    </div>
  )
}
