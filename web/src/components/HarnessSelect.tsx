import { FieldHelp } from "@/components/FieldHelp"
import { SegmentedControl } from "@/components/FormControls"
import { Label } from "@/components/ui/label"
import { useConfig } from "@/lib/queries"

export interface HarnessSelectProps {
  idPrefix: string
  value: string
  onChange: (value: string) => void
  allowDefault?: boolean
  defaultLabel?: string
  variant?: "segmented" | "select"
  label?: string
  description?: string
}

/** Harness selector (segmented buttons or dropdown) backed by GET /api/config. */
export function HarnessSelect({
  idPrefix,
  value,
  onChange,
  allowDefault = true,
  defaultLabel,
  variant = "segmented",
  label = "Harness",
  description = "AI harness used for this run (defaults to Settings).",
}: HarnessSelectProps) {
  const { data: cfg } = useConfig()
  const def = (cfg?.default_harness ?? "") || "agy"
  const list = cfg?.harnesses ?? [
    { name: "agy", label: "Antigravity (agy-guard)", binary: "agy-guard", available: true },
    { name: "omp", label: "OMP (omp)", binary: "omp", available: true },
  ]
  const options = [
    ...(allowDefault ? [{ value: "", label: defaultLabel ?? `Default (${def})`, hint: `Use configured default (${def})` }] : []),
    ...list.map((h) => ({
      value: h.name,
      label: h.available ? h.name : `${h.name} (missing)`,
      hint: h.available ? h.label : `${h.binary} not found on PATH`,
    })),
  ]
  if (variant === "select") {
    return (
      <div className="grid gap-1.5">
        <Label htmlFor={`${idPrefix}-harness`}>
          <FieldHelp label={label} flag="--harness" description={description} />
        </Label>
        <select
          id={`${idPrefix}-harness`}
          aria-label={label}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring"
        >
          {options.map((o) => (
            <option key={o.value} value={o.value} title={o.hint}>{o.label}</option>
          ))}
        </select>
      </div>
    )
  }
  return (
    <SegmentedControl
      idPrefix={`${idPrefix}-harness`}
      label={label}
      hint={description}
      options={options}
      value={value}
      onChange={onChange}
    />
  )
}
