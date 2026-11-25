import { cloneElement, isValidElement, useId, useState, type ReactElement, type ReactNode } from "react"
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
  const tooltipId = useId()
  if (!content) return <>{children}</>

  const child = isValidElement<{ "aria-describedby"?: string }>(children)
    ? cloneElement(children as ReactElement<{ "aria-describedby"?: string }>, {
        "aria-describedby": open
          ? [children.props["aria-describedby"], tooltipId].filter(Boolean).join(" ")
          : children.props["aria-describedby"],
      })
    : children

  return (
    <span
      className={cn("relative inline-flex", className)}
      title={content}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      {child}
      {open ? (
        <span
          id={tooltipId}
          role="tooltip"
          className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-1.5 w-max max-w-60 -translate-x-1/2 rounded-md border border-border bg-popover px-2.5 py-1 text-xs text-popover-foreground shadow-md"
        >
          {content}
        </span>
      ) : null}
    </span>
  )
}
