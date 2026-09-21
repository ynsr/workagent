import { useEffect, useMemo, useRef, useState } from "react"
import { Loader2, RefreshCw, TriangleAlert } from "lucide-react"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

export interface AsyncOptions {
  options: string[]
  warning?: string
}

export function SearchableSelect({ value, options, onChange, placeholder, allowCustom, fetchOptions, mapOption, id }: {
  value: string
  options: string[]
  onChange: (v: string) => void
  placeholder?: string
  /** Escape hatch: submit a raw value outside the option list (backends that accept free text). */
  allowCustom?: boolean
  /** Async mode: extra options loaded once on mount (client-side filtered
   * with the sync `options`). Loading/error/warning states included. */
  fetchOptions?: () => Promise<AsyncOptions>
  /** Transform option-list selections before onChange (e.g. strip a display
   * suffix). Free-text commits pass through untouched. */
  mapOption?: (o: string) => string
  /** Forwarded to the inner filter input (label association). */
  id?: string
}) {
  const [q, setQ] = useState("")
  const [fetched, setFetched] = useState<string[] | null>(null)
  const [warning, setWarning] = useState<string | undefined>(undefined)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fetchRef = useRef(fetchOptions)
  fetchRef.current = fetchOptions

  useEffect(() => {
    if (!fetchRef.current) return
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchRef
      .current()
      .then((res) => {
        if (cancelled) return
        setFetched(res.options)
        setWarning(res.warning)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : "Failed to load options")
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  function retry() {
    const fn = fetchRef.current
    if (!fn) return
    setLoading(true)
    setError(null)
    fn()
      .then((res) => {
        setFetched(res.options)
        setWarning(res.warning)
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to load options")
      })
      .finally(() => setLoading(false))
  }

  const all = useMemo(() => {
    if (!fetched) return options
    const seen: Record<string, true> = {}
    for (const o of options) seen[o] = true
    return [...options, ...fetched.filter((o) => !seen[o])]
  }, [options, fetched])

  const custom = q.trim()
  const known = all.includes(custom)
  const filtered = useMemo(
    () => all.filter((o) => o.toLowerCase().includes(q.toLowerCase())),
    [all, q],
  )
  // Show the selected value when the filter is empty so a picked option
  // stays visible; typing always takes precedence over the shown value.
  const shown = q || value
  function pick(o: string) {
    onChange(mapOption ? mapOption(o) : o)
    setQ("")
  }
  function commitCustom() {
    onChange(custom)
    setQ("")
  }
  return (
    <div>
      <Input
        id={id}
        value={shown}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && allowCustom && custom && !known) {
            e.preventDefault()
            commitCustom()
          }
        }}
        placeholder={placeholder ?? "Type to filter…"}
      />
      {loading ? (
        <p className="flex items-center gap-1.5 py-2 text-sm text-muted-foreground" role="status">
          <Loader2 aria-hidden className="size-4 animate-spin" /> Loading options…
        </p>
      ) : null}
      {error ? (
        <p className="flex items-center gap-2 py-2 text-sm text-destructive" role="alert">
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
      <div className="max-h-40 overflow-auto">
        {allowCustom && custom && !known ? (
          <button type="button" onClick={commitCustom}
            className={cn("block w-full text-left", custom === value && "font-bold")}>
            Use &ldquo;{custom}&rdquo;
          </button>
        ) : null}
        {filtered.map((o) => (
          <button key={o} type="button" onClick={() => pick(o)}
            className={cn("block w-full text-left", o === value && "font-bold")}>
            {o}
          </button>
        ))}
      </div>
    </div>
  )
}
