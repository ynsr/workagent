import { Label } from "@/components/ui/label"
import { FieldHelp } from "@/components/FieldHelp"
import { useHarnessOptions } from "@/hooks/useHarnessOptions"
import type { HarnessOption } from "@/lib/api"

function OptionSelect({
  id,
  label,
  flag,
  description,
  value,
  options,
  current,
  disabled,
  disabledReason,
  onChange,
}: {
  id: string
  label: string
  flag: string
  description: string
  value: string
  options: HarnessOption[]
  current: string
  disabled: boolean
  disabledReason?: string
  onChange: (v: string) => void
}) {
  return (
    <div className="grid gap-1.5" title={disabled ? disabledReason : undefined}>
      <Label htmlFor={id}>
        <FieldHelp label={label} flag={flag} description={description} />
      </Label>
      <select
        id={id}
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring disabled:cursor-not-allowed disabled:opacity-50"
      >
        <option value="">Default{current ? ` (${current})` : ""}</option>
        {options.map((o) => (
          <option key={o.id} value={o.id} title={o.name !== o.id ? o.name : undefined}>
            {o.name || o.id}
          </option>
        ))}
      </select>
      {disabled && disabledReason ? (
        <p className="text-xs text-muted-foreground">{disabledReason}</p>
      ) : null}
    </div>
  )
}

/** Model selector driven by ACP `session/new` discovery (issue #112).
 * Shown for all 4 run modes; disabled with tooltip when the harness
 * returns no models or discovery failed. Empty = harness default. */
export function ModelSelect({
  idPrefix,
  harness,
  value,
  onChange,
}: {
  idPrefix: string
  harness: string
  value: string
  onChange: (v: string) => void
}) {
  const { data, isPending, isError } = useHarnessOptions(harness)
  const models = data?.models ?? []
  const disabled = isPending || isError || !data?.supported || models.length === 0
  const reason = isPending
    ? "Loading model options…"
    : isError || !data
      ? "Model discovery failed — using harness default"
      : !data.supported
        ? "This harness does not support model discovery — using harness default"
        : "No models reported — using harness default"
  return (
    <OptionSelect
      id={`${idPrefix}-model`}
      label="Model"
      flag="--model"
      description="Harness model override (empty = harness default; same for preview, headless, ACP, terminal)."
      value={value}
      options={models}
      current={data?.current_model ?? ""}
      disabled={disabled}
      disabledReason={reason}
      onChange={onChange}
    />
  )
}

/** Effort selector driven by ACP `session/new` discovery (issue #112).
 * Same availability rules as ModelSelect; empty = harness default. */
export function EffortSelect({
  idPrefix,
  harness,
  value,
  onChange,
}: {
  idPrefix: string
  harness: string
  value: string
  onChange: (v: string) => void
}) {
  const { data, isPending, isError } = useHarnessOptions(harness)
  const efforts = data?.efforts ?? []
  const disabled = isPending || isError || !data?.supported || efforts.length === 0
  const reason = isPending
    ? "Loading effort options…"
    : isError || !data
      ? "Effort discovery failed — using harness default"
      : !data.supported
        ? "This harness does not support effort discovery — using harness default"
        : "No efforts reported — using harness default"
  return (
    <OptionSelect
      id={`${idPrefix}-effort`}
      label="Effort"
      flag="--effort"
      description="Reasoning effort override (empty = harness default; same for preview, headless, ACP, terminal)."
      value={value}
      options={efforts}
      current={data?.current_effort ?? ""}
      disabled={disabled}
      disabledReason={reason}
      onChange={onChange}
    />
  )
}
