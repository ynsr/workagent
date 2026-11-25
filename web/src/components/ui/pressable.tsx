import * as React from "react"
import { cn } from "@/lib/utils"

/** Minimalist, borderless interactive row for divided lists inside popovers. */
const Pressable = React.forwardRef<
  HTMLButtonElement,
  React.ComponentProps<"button">
>(function Pressable({ className, type = "button", ...props }, ref) {
  return (
    <button
      ref={ref}
      type={type}
      data-slot="pressable"
      className={cn(
        "flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm text-popover-foreground transition-colors",
        "hover:bg-accent hover:text-accent-foreground",
        "focus-visible:bg-accent focus-visible:text-accent-foreground focus-visible:outline-none",
        "disabled:pointer-events-none disabled:opacity-50",
        "aria-[disabled=true]:cursor-not-allowed aria-[disabled=true]:opacity-50",
        className,
      )}
      {...props}
    />
  )
})

export { Pressable }
