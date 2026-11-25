import { useState, type ReactNode } from "react"
import { cn } from "@/lib/utils"

/** Lightweight accessible tooltip for disabled reasons and icon hints.
 * Preserves native `title` fallback alongside a theme-aware floating label. */
export function Tooltip({
  content,
  children,
  className,
}: {
  content?: string
  children: ReactNode
  className?: string
}) {
  const [open, setOpen] = useState(false)
  if (!content) return <>{children}</>
  return (
    <span
      className={cn("relative inline-flex", className)}
      title={content}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      {children}
      {open ? (
        <span
          role="tooltip"
          className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-1.5 w-max max-w-60 -translate-x-1/2 rounded-md border border-border bg-popover px-2.5 py-1 text-xs text-popover-foreground shadow-md"
        >
          {content}
        </span>
      ) : null}
    </span>
  )
}
