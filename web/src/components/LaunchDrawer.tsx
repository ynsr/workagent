import { useEffect, useId, useRef, useState, type ReactNode } from "react"
import { X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",")

function getFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => !el.hasAttribute("disabled") && el.getAttribute("aria-hidden") !== "true",
  )
}

export interface LaunchDrawerProps {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  children: ReactNode
  className?: string
}

/**
 * Reusable right-hand slide-over drawer with focus trap, Esc close,
 * trigger-focus restoration, and `prefers-reduced-motion` support.
 */
export function LaunchDrawer({
  open,
  onClose,
  title,
  description,
  children,
  className,
}: LaunchDrawerProps) {
  const titleId = useId()
  const descId = useId()
  const panelRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLElement | null>(null)
  const wasOpenRef = useRef(false)
  const [entered, setEntered] = useState(false)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  // Trigger slide-in transition on the next frame after mount and lock body scroll while open.
  useEffect(() => {
    if (!open) {
      setEntered(false)
      return
    }
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = "hidden"
    const raf = requestAnimationFrame(() => setEntered(true))
    return () => {
      cancelAnimationFrame(raf)
      document.body.style.overflow = prevOverflow
    }
  }, [open])

  // Capture trigger element on open, focus inside drawer, and restore focus on close.
  useEffect(() => {
    if (open) {
      if (!wasOpenRef.current) {
        const active = document.activeElement
        triggerRef.current = active instanceof HTMLElement ? active : null
      }
      wasOpenRef.current = true

      const panel = panelRef.current
      if (panel) {
        const focusables = getFocusableElements(panel)
        const firstFocusable = focusables[0]
        if (firstFocusable) {
          firstFocusable.focus()
        } else {
          panel.focus()
        }
      }
    } else if (wasOpenRef.current) {
      wasOpenRef.current = false
      const trigger = triggerRef.current
      triggerRef.current = null
      if (trigger && trigger.isConnected) {
        trigger.focus()
      }
    }
  }, [open])

  // Restore focus if unmounted while open.
  useEffect(() => {
    return () => {
      if (wasOpenRef.current) {
        const trigger = triggerRef.current
        if (trigger && trigger.isConnected) {
          trigger.focus()
        }
      }
    }
  }, [])

  // Keyboard handling: Escape to close + Tab focus trap.
  useEffect(() => {
    if (!open) return

    function handleKeyDown(e: KeyboardEvent) {
      const panel = panelRef.current
      if (!panel) return

      if (e.key === "Escape") {
        if (e.defaultPrevented) return
        // Do not close if focus is inside a nested portal/dialog outside this drawer.
        const active = document.activeElement
        if (active && active !== document.body && !panel.contains(active)) return
        e.preventDefault()
        onCloseRef.current()
        return
      }

      if (e.key === "Tab") {
        const active = document.activeElement
        if (active && active !== document.body && !panel.contains(active)) return
        const focusables = getFocusableElements(panel)
        if (focusables.length === 0) {
          e.preventDefault()
          panel.focus()
          return
        }
        const first = focusables[0]
        const last = focusables[focusables.length - 1]
        if (!first || !last) return
        if (e.shiftKey) {
          if (active === first || active === panel || !panel.contains(active)) {
            e.preventDefault()
            last.focus()
          }
        } else if (active === last || !panel.contains(active)) {
          e.preventDefault()
          first.focus()
        }
      }
    }

    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [open])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div
        data-testid="launch-drawer-backdrop"
        aria-hidden="true"
        onClick={onClose}
        className={cn(
          "fixed inset-0 bg-black/50 backdrop-blur-[1px] motion-safe:transition-opacity motion-safe:duration-200 motion-reduce:transition-none",
          entered ? "opacity-100" : "motion-safe:opacity-0",
        )}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descId : undefined}
        tabIndex={-1}
        className={cn(
          "relative z-10 flex h-full w-full max-w-2xl flex-col border-l bg-background text-foreground shadow-xl outline-none",
          "motion-safe:transition-transform motion-safe:duration-200 motion-reduce:transition-none",
          entered ? "translate-x-0" : "motion-safe:translate-x-full",
          className,
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b px-6 py-4">
          <div className="min-w-0">
            <h2 id={titleId} className="text-lg font-semibold tracking-tight">
              {title}
            </h2>
            {description ? (
              <p id={descId} className="mt-0.5 text-sm text-muted-foreground">
                {description}
              </p>
            ) : null}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0"
          >
            <X aria-hidden className="size-4" />
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5">{children}</div>
      </div>
    </div>
  )
}
