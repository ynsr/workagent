import type { ReactNode } from "react"
import { CircleHelp } from "lucide-react"

/** Human-readable form label with a (?) hover icon carrying the CLI flag + description. */
export function FieldHelp({ label, flag, description }: {
  label: string
  flag: string
  description: string
}) {
  return (
    <span className="inline-flex items-center gap-1">
      <span>{label}</span>
      <span
        className="inline-flex cursor-help items-center text-muted-foreground hover:text-foreground"
        title={`${flag} — ${description}`}
        aria-label={`${label}: ${flag} — ${description}`}
      >
        <CircleHelp aria-hidden className="size-3.5" />
      </span>
    </span>
  )
}

/** Checkbox row: human label + (?) tooltip, no flag text inline. */
export function CheckRow({ id, checked, onChange, label, flag, description }: {
  id: string
  checked: boolean
  onChange: (v: boolean) => void
  label: string
  flag: string
  description: string
}) {
  return (
    <div className="flex items-center gap-2">
      <input type="checkbox" id={id} checked={checked} onChange={(e) => onChange(e.target.checked)} className="size-4 accent-primary" />
      <label htmlFor={id} className="inline-flex items-center gap-1 text-sm font-normal">
        <span>{label}</span>
        <span
          className="inline-flex cursor-help items-center text-muted-foreground hover:text-foreground"
          title={`${flag} — ${description}`}
          aria-label={`${label}: ${flag} — ${description}`}
        >
          <CircleHelp aria-hidden className="size-3.5" />
        </span>
      </label>
    </div>
  )
}

export function FieldLabel({ children }: { children: ReactNode }) {
  return <span className="inline-flex items-center gap-1">{children}</span>
}
