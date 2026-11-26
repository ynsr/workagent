import type { ReactNode } from "react"
import { CircleHelp } from "lucide-react"

import { Tooltip } from "@/components/ui/tooltip"

function HelpIcon({ label, flag, description }: { label: string; flag: string; description: string }) {
  const tip = `${flag} — ${description}`
  return (
    <Tooltip label={tip}>
      <span
        role="img"
        tabIndex={0}
        className="inline-flex cursor-help items-center rounded-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50"
        aria-label={`${label}: ${tip}`}
      >
        <CircleHelp aria-hidden className="size-3.5" />
      </span>
    </Tooltip>
  )
}

/** Human-readable form label with a (?) tooltip carrying the CLI flag + description. */
export function FieldHelp({ label, flag, description }: {
  label: string
  flag: string
  description: string
}) {
  return (
    <span className="inline-flex items-center gap-1">
      <span>{label}</span>
      <HelpIcon label={label} flag={flag} description={description} />
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
      </label>
      <HelpIcon label={label} flag={flag} description={description} />
    </div>
  )
}

export function FieldLabel({ children }: { children: ReactNode }) {
  return <span className="inline-flex items-center gap-1">{children}</span>
}
