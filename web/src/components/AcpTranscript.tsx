import { useMemo } from "react"
import { parseAcpBlocks, type AcpBlock } from "@/lib/harness"
import { Badge } from "@/components/ui/badge"
import type { RunLine } from "@/lib/api"

function BlockView({ block }: { block: AcpBlock }) {
  switch (block.kind) {
    case "thought":
      return <div className="text-white/50 italic">{block.text}</div>
    case "tool_call":
      return (
        <div className="rounded border border-white/10 bg-white/5 px-2 py-1">
          <span className="text-sky-300">⚙ {block.name ?? block.toolKind ?? "tool"}</span>
          {block.status ? <Badge className="ml-2">{block.status}</Badge> : null}
          {block.text ? <div className="whitespace-pre-wrap">{block.text}</div> : null}
        </div>
      )
    case "usage":
      return <div className="text-xs text-white/40">{block.text}</div>
    case "question":
      return (
        <div className="rounded border border-sky-500/40 bg-sky-500/10 px-2 py-1" data-testid="acp-question">
          <span className="text-sky-300">❓ {block.text}</span>
        </div>
      )
    case "turn_end":
      return <div className="text-white/30">— turn end —</div>
    default:
      return <div className="whitespace-pre-wrap break-words">{block.text || " "}</div>
  }
}

/** Structured ACP transcript (issue #112): parsed ACP blocks with a Raw toggle. */
export function AcpTranscript({ lines }: { lines: RunLine[] }) {
  const blocks = useMemo(() => parseAcpBlocks(lines), [lines])
  const structured = blocks.filter((b) => b.kind !== "raw").length
  if (structured === 0) return null
  return (
    <div className="mb-4 rounded-lg border" data-testid="acp-transcript">
      <div className="border-b border-white/10 px-3 py-2 text-xs text-white/70">
        ACP transcript — {structured} structured event{structured === 1 ? "" : "s"} ({blocks.length} blocks)
      </div>
      <div className="flex max-h-[40vh] flex-col gap-1.5 overflow-auto px-3 py-2.5 font-mono text-[12.5px] leading-relaxed">
        {blocks.map((b, i) => (
          <BlockView key={i} block={b} />
        ))}
      </div>
    </div>
  )
}
