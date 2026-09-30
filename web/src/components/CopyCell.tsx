import type { ReactNode } from "react"
import { toast } from "sonner"
import { Copy } from "lucide-react"
import { copyToClipboard } from "@/lib/format"
import { errorText } from "@/components/StatusFeedback"
import { cn } from "@/lib/utils"

/**
 * Hover-overlay copy button for truncated text cells (issue #34).
 * Renders the text truncated with ellipsis; on hover a copy icon overlays
 * the left side. Disabled for empty/null values. Anchor content passes
 * through untouched (callers skip anchors per the issue spec).
 */
export function CopyCell({
  text,
  copyText,
  title,
  className,
  children,
}: {
  text: string | null | undefined
  /**
   * Actual string written to the clipboard. Defaults to `text`.
   * Use when the displayed text is shortened (e.g. worktree path with the
   * `$HOME/dev/worktrees/` prefix stripped) but the full value must be copied.
   */
  copyText?: string | null | undefined
  title?: string
  className?: string
  children?: ReactNode
}) {
  const value = text ?? ""
  const toCopy = copyText ?? text ?? ""
  const empty = toCopy.trim() === ""
  return (
    <span className={cn("group/copy relative flex min-w-0 items-center", className)}>
      <span className="min-w-0 flex-1 truncate" title={title ?? (empty ? undefined : toCopy)}>
        {children ?? (empty ? "—" : value)}
      </span>
      <button
        type="button"
        aria-label={empty ? "Nothing to copy" : `Copy ${toCopy}`}
        title={empty ? "Nothing to copy" : `Copy ${toCopy}`}
        disabled={empty}
        onClick={(e) => {
          e.stopPropagation()
          void copyToClipboard(toCopy)
            .then(() => toast.success("Copied to clipboard"))
            .catch((err: unknown) => toast.error(errorText(err)))
        }}
        className="absolute left-0 hidden size-6 items-center justify-center rounded border bg-card/95 shadow-sm backdrop-blur transition group-hover/copy:flex focus-visible:flex disabled:cursor-not-allowed disabled:opacity-40 hover:bg-accent hover:text-accent-foreground active:scale-90 [@media(hover:none)]:flex"
      >
        <Copy aria-hidden className="size-3.5" />
      </button>
    </span>
  )
}
