import { useMemo, useState } from "react"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

export function SearchableSelect({ value, options, onChange, placeholder }: {
  value: string
  options: string[]
  onChange: (v: string) => void
  placeholder?: string
}) {
  const [q, setQ] = useState("")
  const filtered = useMemo(
    () => options.filter((o) => o.toLowerCase().includes(q.toLowerCase())),
    [options, q],
  )
  return (
    <div>
      <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder ?? "Type to filter…"} />
      <div className="max-h-40 overflow-auto">
        {filtered.map((o) => (
          <button key={o} type="button" onClick={() => onChange(o)}
            className={cn("block w-full text-left", o === value && "font-bold")}>
            {o}
          </button>
        ))}
      </div>
    </div>
  )
}
