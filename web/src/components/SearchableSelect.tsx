import { useMemo, useState } from "react"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

export function SearchableSelect({ value, options, onChange, placeholder, allowCustom }: {
  value: string
  options: string[]
  onChange: (v: string) => void
  placeholder?: string
  /** Escape hatch: submit a raw value outside the option list (backends that accept free text). */
  allowCustom?: boolean
}) {
  const [q, setQ] = useState("")
  const custom = q.trim()
  const known = options.includes(custom)
  const filtered = useMemo(
    () => options.filter((o) => o.toLowerCase().includes(q.toLowerCase())),
    [options, q],
  )
  function commitCustom() {
    onChange(custom)
    setQ("")
  }
  return (
    <div>
      <Input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && allowCustom && custom && !known) {
            e.preventDefault()
            commitCustom()
          }
        }}
        placeholder={placeholder ?? "Type to filter…"}
      />
      <div className="max-h-40 overflow-auto">
        {allowCustom && custom && !known ? (
          <button type="button" onClick={commitCustom}
            className={cn("block w-full text-left", custom === value && "font-bold")}>
            Use &ldquo;{custom}&rdquo;
          </button>
        ) : null}
        {filtered.map((o) => (
          <button key={o} type="button" onClick={() => { onChange(o); setQ("") }}
            className={cn("block w-full text-left", o === value && "font-bold")}>
            {o}
          </button>
        ))}
      </div>
    </div>
  )
}
