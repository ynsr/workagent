import * as React from "react"
import * as TooltipPrimitive from "@radix-ui/react-tooltip"

import { cn } from "@/lib/utils"

/** One-phrase tooltip for icon-only controls and disabled hints (design/02 section 2.8). */
export function Tooltip({
  label,
  content,
  children,
  side = "top",
  className,
}: {
  label?: React.ReactNode
  content?: React.ReactNode
  children: React.ReactNode
  side?: "top" | "bottom" | "left" | "right"
  className?: string
}) {
  const tooltipContent = label ?? content
  if (!tooltipContent) return <>{children}</>

  const triggerChild =
    className || !React.isValidElement(children) ? (
      <span className={cn("inline-flex", className)}>{children}</span>
    ) : (
      children
    )

  return (
    <TooltipPrimitive.Provider delayDuration={400}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{triggerChild}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content
            side={side}
            sideOffset={6}
            className={cn(
              "z-50 max-w-xs rounded-md bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md ring-1 ring-foreground/5",
            )}
          >
            {tooltipContent}
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  )
}
