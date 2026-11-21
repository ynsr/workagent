import { useEffect, useRef, useState } from "react"

export interface HotkeyBinding {
  /** Key spec: e.g. "Mod+K", "Mod+F", "/", "j", "k", "Enter", "s", "r", "c", "Escape". */
  key: string
  /** Handler invoked when the key matches. */
  handler: (event: KeyboardEvent) => void
  /** Allow firing while focus is inside an input, textarea, select, or contenteditable. Default: false. */
  allowInInputs?: boolean
  /** Call event.preventDefault() when matched. Default: true. */
  preventDefault?: boolean
  /** Disable this binding dynamically. Default: true. */
  enabled?: boolean
}

/** Returns true when the event target (or activeElement) is an editable form control. */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false
  const tag = target.tagName
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true
  if (target.isContentEditable) return true
  const ce = target.getAttribute("contenteditable")
  if (ce !== null && ce.toLowerCase() !== "false") return true
  return false
}

interface ParsedKeySpec {
  mod: boolean
  ctrl: boolean
  meta: boolean
  alt: boolean
  shift: boolean
  key: string
}

function parseKeySpec(spec: string): ParsedKeySpec {
  const parts = spec.split("+").map((p) => p.trim())
  const rawKey = parts.pop() ?? ""
  const mods = new Set(parts.map((p) => p.toLowerCase()))
  return {
    mod: mods.has("mod"),
    ctrl: mods.has("ctrl") || mods.has("control"),
    meta: mods.has("meta") || mods.has("cmd") || mods.has("command"),
    alt: mods.has("alt") || mods.has("option"),
    shift: mods.has("shift"),
    key: rawKey.length === 1 ? rawKey.toLowerCase() : rawKey.toLowerCase(),
  }
}

export function matchesKeySpec(event: KeyboardEvent, spec: string): boolean {
  const parsed = parseKeySpec(spec)
  const eventKey = event.key.toLowerCase()
  if (eventKey !== parsed.key) return false

  if (parsed.mod) {
    if (!(event.metaKey || event.ctrlKey)) return false
  } else {
    if (event.ctrlKey !== parsed.ctrl) return false
    if (event.metaKey !== parsed.meta) return false
  }

  if (event.altKey !== parsed.alt) return false

  // For single punctuation keys like "/" or "?" don't require shift=false if shift produced the char,
  // but for letters/named keys enforce shift unless specified.
  if (parsed.shift) {
    if (!event.shiftKey) return false
  } else if (/^[a-z]$/.test(parsed.key) && event.shiftKey) {
    return false
  }

  return true
}

/**
 * Register global window keydown bindings with automatic suppression when typing
 * in inputs, textareas, selects, or contenteditable elements.
 */
export function useHotkeys(bindings: HotkeyBinding[], enabled = true): void {
  const bindingsRef = useRef(bindings)
  bindingsRef.current = bindings

  useEffect(() => {
    if (!enabled) return

    function onKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented) return
      const inEditable = isEditableTarget(event.target)

      for (const binding of bindingsRef.current) {
        if (binding.enabled === false) continue
        if (inEditable && !binding.allowInInputs) continue
        if (!matchesKeySpec(event, binding.key)) continue

        if (binding.preventDefault !== false) {
          event.preventDefault()
        }
        binding.handler(event)
        return
      }
    }

    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [enabled])
}

export interface ListHotkeysOptions<T> {
  items: readonly T[]
  onOpen?: (item: T, index: number) => void
  onSync?: (item: T, index: number) => void
  onReview?: (item: T, index: number) => void
  onCleanup?: (item: T, index: number) => void
  onFocusSearch?: () => void
  enabled?: boolean
}

/**
 * Shared j/k/Enter (+ optional s/r/c and / search focus) keyboard navigation
 * for list and table views.
 */
export function useListHotkeys<T>({
  items,
  onOpen,
  onSync,
  onReview,
  onCleanup,
  onFocusSearch,
  enabled = true,
}: ListHotkeysOptions<T>) {
  const [activeIndex, setActiveIndex] = useState<number>(-1)

  useEffect(() => {
    setActiveIndex((cur) => {
      if (items.length === 0) return -1
      if (cur >= items.length) return items.length - 1
      return cur
    })
  }, [items.length])

  const activeItem = activeIndex >= 0 && activeIndex < items.length ? items[activeIndex] : undefined

  const bindings: HotkeyBinding[] = [
    {
      key: "j",
      handler: () => {
        if (items.length === 0) return
        setActiveIndex((cur) => (cur < 0 ? 0 : Math.min(cur + 1, items.length - 1)))
      },
    },
    {
      key: "k",
      handler: () => {
        if (items.length === 0) return
        setActiveIndex((cur) => (cur <= 0 ? 0 : cur - 1))
      },
    },
    {
      key: "Enter",
      enabled: Boolean(onOpen && activeItem !== undefined),
      handler: () => {
        if (onOpen && activeItem !== undefined) onOpen(activeItem, activeIndex)
      },
    },
    {
      key: "s",
      enabled: Boolean(onSync && activeItem !== undefined),
      handler: () => {
        if (onSync && activeItem !== undefined) onSync(activeItem, activeIndex)
      },
    },
    {
      key: "r",
      enabled: Boolean(onReview && activeItem !== undefined),
      handler: () => {
        if (onReview && activeItem !== undefined) onReview(activeItem, activeIndex)
      },
    },
    {
      key: "c",
      enabled: Boolean(onCleanup && activeItem !== undefined),
      handler: () => {
        if (onCleanup && activeItem !== undefined) onCleanup(activeItem, activeIndex)
      },
    },
    {
      key: "/",
      enabled: Boolean(onFocusSearch),
      handler: () => {
        onFocusSearch?.()
      },
    },
  ]

  useHotkeys(bindings, enabled)

  return { activeIndex, setActiveIndex, activeItem }
}
