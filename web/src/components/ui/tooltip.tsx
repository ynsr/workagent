import * as React from "react"
import * as TooltipPrimitive from "@radix-ui/react-tooltip"

import { cn } from "@/lib/utils"

const TooltipProviderContext = React.createContext(false)

/** Shared provider so adjacent tooltips reuse Radix's skipDelayDuration window. */
export function TooltipProvider({
  delayDuration = 400,
  skipDelayDuration = 300,
  children,
}: {
  delayDuration?: number
  skipDelayDuration?: number
  children: React.ReactNode
}) {
  return (
    <TooltipProviderContext.Provider value={true}>
      <TooltipPrimitive.Provider delayDuration={delayDuration} skipDelayDuration={skipDelayDuration}>
        {children}
      </TooltipPrimitive.Provider>
    </TooltipProviderContext.Provider>
  )
}

/** One-phrase tooltip for icon-only controls and disabled hints (design/02 section 2.8). Keyboard-focusable via the trigger. */
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
  const hasProvider = React.useContext(TooltipProviderContext)
  const tooltipContent = label ?? content
  if (!tooltipContent) return <>{children}</>

  const triggerChild = React.isValidElement<{ className?: string }>(children) ? (
    className ? (
      React.cloneElement(children, {
        className: cn(children.props.className, className),
      })
    ) : (
      children
    )
  ) : (
    <span className={cn("inline-flex", className)}>{children}</span>
  )

  const tooltip = (
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
  )
  return hasProvider ? tooltip : <TooltipProvider>{tooltip}</TooltipProvider>
}
