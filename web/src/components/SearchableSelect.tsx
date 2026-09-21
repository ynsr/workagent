import { useEffect, useMemo, useRef, useState } from "react"
import { Check, ChevronsUpDown, Loader2, RefreshCw, TriangleAlert } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/utils"

export interface AsyncOptions {
  options: string[]
  warning?: string
}

/**
 * Searchable combobox: a button trigger opens a Radix Popover whose panel
 * holds the filter input + option list. Nothing renders until the trigger
 * is clicked/focused-opened; selecting or Enter commits and closes.
 */
export function SearchableSelect({ value, options, onChange, placeholder, allowCustom, fetchOptions, mapOption, id }: {
  value: string
  options: string[]
  onChange: (v: string) => void
  placeholder?: string
  /** Escape hatch: submit a raw value outside the option list (backends that accept free text). */
  allowCustom?: boolean
  /** Async mode: extra options loaded on first open (client-side filtered
   * with the sync `options`). Loading/error/warning states included. */
  fetchOptions?: () => Promise<AsyncOptions>
  /** Transform option-list selections before onChange (e.g. strip a display
   * suffix). Free-text commits pass through untouched. */
  mapOption?: (o: string) => string
  /** Forwarded to the trigger button (label association). */
  id?: string
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState("")
  const [fetched, setFetched] = useState<string[] | null>(null)
  const [warning, setWarning] = useState<string | undefined>(undefined)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fetchRef = useRef(fetchOptions)
  fetchRef.current = fetchOptions
  const loadedRef = useRef(false)

  function load() {
    const fn = fetchRef.current
    if (!fn || loadedRef.current) return
    loadedRef.current = true
    setLoading(true)
    setError(null)
    fn()
      .then((res) => {
        setFetched(res.options)
        setWarning(res.warning)
      })
      .catch((err: unknown) => {
        loadedRef.current = false
        setError(err instanceof Error ? err.message : "Failed to load options")
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (open) {
      load()
      setQ("")
    }
  }, [open])

  function retry() {
    loadedRef.current = false
    load()
  }

  const all = useMemo(() => {
    if (!fetched) return options
    const seen: Record<string, true> = {}
    for (const o of options) seen[o] = true
    return [...options, ...fetched.filter((o) => !seen[o])]
  }, [options, fetched])

  const query = q.trim()
  const known = all.includes(query)
  const filtered = useMemo(
    () => (query ? all.filter((o) => o.toLowerCase().includes(query.toLowerCase())) : all),
    [all, query],
  )

  function pick(o: string) {
    onChange(mapOption ? mapOption(o) : o)
    setOpen(false)
  }

  function commitCustom() {
    if (!query) return
    onChange(query)
    setOpen(false)
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-full justify-between font-normal"
        >
          <span className="truncate">{value ? value : (placeholder ?? "Select…")}</span>
          <ChevronsUpDown aria-hidden className="ml-2 size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="p-1">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && allowCustom && query && !known) {
              e.preventDefault()
              commitCustom()
            }
          }}
          placeholder={placeholder ?? "Type to filter…"}
          autoComplete="off"
          spellCheck={false}
        />
        {loading ? (
          <p className="flex items-center gap-1.5 px-2 py-2 text-sm text-muted-foreground" role="status">
            <Loader2 aria-hidden className="size-4 animate-spin" /> Loading options…
          </p>
        ) : null}
        {error ? (
          <p className="flex items-center gap-2 px-2 py-2 text-sm text-destructive" role="alert">
            <span>{error}</span>
            <button
              type="button"
              onClick={retry}
              className="inline-flex items-center gap-1 underline underline-offset-2"
            >
              <RefreshCw aria-hidden className="size-3.5" /> Retry
            </button>
          </p>
        ) : null}
        {warning ? (
          <p className="mt-1.5 flex items-start gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-800 dark:text-amber-200">
            <TriangleAlert aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            <span>{warning}</span>
          </p>
        ) : null}
        <div className="max-h-40 overflow-auto py-1">
          {allowCustom && query && !known ? (
            <button
              type="button"
              onClick={commitCustom}
              className="block w-full rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent hover:text-accent-foreground"
            >
              Use &ldquo;{query}&rdquo;
            </button>
          ) : null}
          {filtered.length === 0 && !(allowCustom && query && !known) ? (
            <p className="px-2 py-1.5 text-sm text-muted-foreground">No matches.</p>
          ) : null}
          {filtered.map((o) => (
            <button
              key={o}
              type="button"
              role="option"
              aria-selected={o === value}
              onClick={() => pick(o)}
              className={cn(
                "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent hover:text-accent-foreground",
                o === value && "font-medium",
              )}
            >
              <Check aria-hidden className={cn("size-4 shrink-0", o === value ? "opacity-100" : "opacity-0")} />
              <span className="truncate">{o}</span>
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  )
}
