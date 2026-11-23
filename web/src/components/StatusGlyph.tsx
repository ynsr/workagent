import { Check, CircleAlert, Circle, GitMerge, GitPullRequest, LoaderCircle, X } from "lucide-react"
import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

export type StatusTone = "working" | "needs-you" | "in-review" | "ready" | "merged" | "failed" | "done" | "idle"

const GLYPHS: Record<StatusTone, { icon: typeof Circle; className: string }> = {
  working: { icon: LoaderCircle, className: "text-status-working animate-spin motion-reduce:animate-none" },
  "needs-you": { icon: CircleAlert, className: "text-status-needs-you" },
  failed: { icon: X, className: "text-destructive" },
  "in-review": { icon: GitPullRequest, className: "text-status-in-review" },
  ready: { icon: GitPullRequest, className: "text-status-ready" },
  merged: { icon: GitMerge, className: "text-status-merged opacity-70" },
  done: { icon: Check, className: "text-status-ready" },
  idle: { icon: Circle, className: "text-muted-foreground" },
}

/** One glyph in a fixed 14px slot; the shape differs per tone so color is never the only signal. */
export function StatusGlyph({ tone, className }: { tone: StatusTone; className?: string }) {
  const { icon: Icon, className: tint } = GLYPHS[tone]
  return <Icon aria-hidden className={cn("size-3.5 shrink-0", tint, className)} />
}

/** Glyph + plain sentence-case label. No border, no fill; color lives on the glyph only. */
export function StatusLabel({
  tone,
  children,
  title,
  className,
}: {
  tone: StatusTone
  children: ReactNode
  title?: string
  className?: string
}) {
  return (
    <span title={title} className={cn("inline-flex items-center gap-1.5 whitespace-nowrap text-xs text-muted-foreground", className)}>
      <StatusGlyph tone={tone} />
      <span>{children}</span>
    </span>
  )
}
