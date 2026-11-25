import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react"
import { Check, ChevronDown, ChevronRight, Cpu, Search, Sparkles, Terminal } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Pressable } from "@/components/ui/pressable"
import { Tooltip } from "@/components/ui/tooltip"
import { useHarnessOptions } from "@/hooks/useHarnessOptions"
import { useConfig } from "@/lib/queries"
import { cn } from "@/lib/utils"

export interface HarnessPickerProps {
  idPrefix?: string
  harness: string
  model?: string
  effort?: string
  onHarnessChange: (harness: string) => void
  onModelChange?: (model: string) => void
  onEffortChange?: (effort: string) => void
  allowDefault?: boolean
  defaultLabel?: string
  harnessOnly?: boolean
  label?: string
  compact?: boolean
  className?: string
}

function HarnessIcon({ name, className }: { name: string; className?: string }) {
  if (name === "agy") return <Sparkles aria-hidden className={cn("size-3.5 shrink-0", className)} />
  if (name === "omp") return <Terminal aria-hidden className={cn("size-3.5 shrink-0", className)} />
  return <Cpu aria-hidden className={cn("size-3.5 shrink-0", className)} />
}

/** Combined minimalist popover picker for Harness, Model, and Effort.
 * Replaces HarnessSelect, ModelSelect, and EffortSelect with a single compact trigger. */
export function HarnessPicker({
  idPrefix,
  harness,
  model = "",
  effort = "",
  onHarnessChange,
  onModelChange,
  onEffortChange,
  allowDefault = true,
  defaultLabel,
  harnessOnly = false,
  label,
  compact = false,
  className,
}: HarnessPickerProps) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")
  const [effortOpen, setEffortOpen] = useState(false)
  const [activeIdx, setActiveIdx] = useState(0)
  const optionIdBase = useId()
  const modelListRef = useRef<HTMLDivElement>(null)
  const pickedInSessionRef = useRef({ model: false, effort: false })

  const { data: cfg } = useConfig()
  const def = (cfg?.default_harness ?? "") || "agy"
  const raw = cfg?.harnesses ?? [
    { name: "agy", label: "Antigravity (agy-guard)", binary: "agy-guard", available: true },
    { name: "omp", label: "OMP (omp)", binary: "omp", available: true },
  ]
  // Issue #112: the AI harness toggle contains only OMP and AGY.
  const list = raw.filter((h) => h.name === "omp" || h.name === "agy")
  const harnessOptions = [
    ...(allowDefault
      ? [{
          value: "",
          label: defaultLabel ?? `Default (${def})`,
          hint: `Use configured default (${def})`,
          available: true,
        }]
      : []),
    ...list.map((h) => ({
      value: h.name,
      label: h.available ? h.name : `${h.name} (missing)`,
      hint: h.available ? h.label : `${h.binary} not found on PATH`,
      available: h.available,
    })),
  ]

  const { data, isPending, isError } = useHarnessOptions(harness, !harnessOnly)
  const models = useMemo(() => data?.models ?? [], [data?.models])
  const efforts = data?.efforts ?? []

  const modelsDisabled = isPending || isError || !data?.supported || models.length === 0
  const modelDisabledReason = isPending
    ? "Loading model options…"
    : isError || !data
      ? "Model discovery failed — using harness default"
      : !data.supported
        ? "This harness does not support model discovery — using harness default"
        : "No models reported — using harness default"

  const effortsDisabled = isPending || isError || !data?.supported || efforts.length === 0
  const effortDisabledReason = isPending
    ? "Loading effort options…"
    : isError || !data
      ? "Effort discovery failed — using harness default"
      : !data.supported
        ? "This harness does not support effort discovery — using harness default"
        : "No efforts reported — using harness default"

  const filteredModels = useMemo(() => {
    const q = search.trim().toLowerCase()
    const defaultRow = {
      id: "",
      name: data?.current_model ? `Default (${data.current_model})` : "Default",
    }
    const matching = q
      ? models.filter((m) => (m.name || m.id).toLowerCase().includes(q) || m.id.toLowerCase().includes(q))
      : models
    if (!q || defaultRow.name.toLowerCase().includes(q) || "default".includes(q)) {
      return [defaultRow, ...matching]
    }
    return matching
  }, [models, search, data?.current_model])

  const selectedHarnessOpt = harnessOptions.find((o) => o.value === harness)
  const harnessDisplay = selectedHarnessOpt?.label ?? (harness || (defaultLabel ?? `Default (${def})`))
  const modelDisplay = model ? (models.find((m) => m.id === model)?.name ?? model) : "Default"
  const effortDisplay = effort ? (efforts.find((e) => e.id === effort)?.name ?? effort) : "Default"
  const triggerText = harnessOnly
    ? harnessDisplay
    : `${harnessDisplay} · ${modelDisplay} · ${effortDisplay}`

  const clampedActiveIdx = filteredModels.length > 0 ? Math.min(activeIdx, filteredModels.length - 1) : 0

  useEffect(() => {
    if (!open || modelsDisabled) return
    const el = modelListRef.current?.children[clampedActiveIdx] as HTMLElement | undefined
    el?.scrollIntoView?.({ block: "nearest" })
  }, [open, modelsDisabled, clampedActiveIdx])

  function handleOpenChange(next: boolean) {
    setOpen(next)
    if (next) {
      setSearch("")
      setActiveIdx(0)
      setEffortOpen(false)
      pickedInSessionRef.current = { model: false, effort: false }
    }
  }

  function selectHarness(next: string) {
    onHarnessChange(next)
    if (next !== harness) {
      setSearch("")
      setActiveIdx(0)
      pickedInSessionRef.current = { model: false, effort: false }
    }
    if (harnessOnly) {
      setOpen(false)
    }
  }

  function pickModel(id: string) {
    onModelChange?.(id)
    pickedInSessionRef.current.model = true
    if (pickedInSessionRef.current.effort || effortsDisabled) {
      setOpen(false)
    }
  }

  function pickEffort(id: string) {
    onEffortChange?.(id)
    setEffortOpen(false)
    pickedInSessionRef.current.effort = true
    if (pickedInSessionRef.current.model || modelsDisabled) {
      setOpen(false)
    }
  }

  function handleSearchKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (modelsDisabled || filteredModels.length === 0) return
    if (e.key === "ArrowDown") {
      e.preventDefault()
      setActiveIdx((i) => Math.min(Math.min(i, filteredModels.length - 1) + 1, filteredModels.length - 1))
    } else if (e.key === "ArrowUp") {
      e.preventDefault()
      setActiveIdx((i) => Math.max(Math.min(i, filteredModels.length - 1) - 1, 0))
    } else if (e.key === "Enter") {
      e.preventDefault()
      const picked = filteredModels[clampedActiveIdx]
      if (picked) {
        pickModel(picked.id)
      }
    }
  }

  const accessibleName = label ?? (harnessOnly ? "Harness" : "Harness, model, and effort")

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <Button
          id={idPrefix ? `${idPrefix}-harness-picker` : undefined}
          type="button"
          variant="outline"
          size={compact ? "sm" : "default"}
          aria-label={`${accessibleName}: ${triggerText}`}
          aria-expanded={open}
          className={cn(
            "justify-between gap-2 font-normal",
            compact ? "h-8 px-2.5 text-xs" : "h-9 px-3 text-sm",
            className,
          )}
        >
          <span className="inline-flex min-w-0 items-center gap-1.5 truncate">
            <HarnessIcon name={harness || def} className="text-muted-foreground" />
            <span className="truncate">{triggerText}</span>
          </span>
          <ChevronDown aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 max-w-[calc(100vw-2rem)] p-0">
        {/* Harness icon toggles */}
        <div
          role="radiogroup"
          aria-label="Harness"
          className={cn("flex items-center gap-1 p-2", !harnessOnly && "border-b border-border")}
        >
          {harnessOptions.map((opt) => {
            const checked = harness === opt.value
            const btn = (
              <button
                key={opt.value}
                type="button"
                role="radio"
                aria-checked={checked}
                title={opt.hint}
                onClick={() => selectHarness(opt.value)}
                className={cn(
                  "inline-flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  checked
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                  !opt.available && !checked && "opacity-70",
                )}
              >
                <HarnessIcon name={opt.value} />
                <span className="truncate">{opt.label}</span>
              </button>
            )
            return !opt.available ? (
              <Tooltip key={opt.value} content={opt.hint} className="flex-1">
                {btn}
              </Tooltip>
            ) : (
              btn
            )
          })}
        </div>

        {!harnessOnly ? (
          <>
            {/* Search input */}
            <div className="flex items-center gap-2 border-b border-border px-3 py-2">
              <Search aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
              <input
                type="text"
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value)
                  setActiveIdx(0)
                }}
                onKeyDown={handleSearchKeyDown}
                placeholder="Search models…"
                aria-label="Search models"
                aria-activedescendant={
                  !modelsDisabled && filteredModels.length > 0
                    ? `${optionIdBase}-model-${clampedActiveIdx}`
                    : undefined
                }
                disabled={modelsDisabled}
                className="w-full bg-transparent text-sm text-popover-foreground outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50"
              />
            </div>

            {/* Divided model list */}
            {modelsDisabled ? (
              <Tooltip content={modelDisabledReason} className="w-full">
                <Pressable aria-disabled="true" aria-label="Model" className="text-xs text-muted-foreground">
                  <span className="truncate">{modelDisabledReason}</span>
                </Pressable>
              </Tooltip>
            ) : filteredModels.length === 0 ? (
              <div className="px-3 py-2.5 text-xs text-muted-foreground">No matching models</div>
            ) : (
              <div
                ref={modelListRef}
                role="listbox"
                aria-label="Model"
                className="max-h-56 divide-y divide-border overflow-y-auto"
              >
                {filteredModels.map((item, idx) => {
                  const selected = model === item.id
                  return (
                    <Pressable
                      key={item.id || "__default__"}
                      id={`${optionIdBase}-model-${idx}`}
                      role="option"
                      aria-selected={selected}
                      onClick={() => pickModel(item.id)}
                      className={cn(idx === clampedActiveIdx && "bg-accent/60")}
                    >
                      <span className="truncate">{item.name || item.id}</span>
                      {selected ? <Check aria-hidden className="size-3.5 shrink-0 text-primary" /> : null}
                    </Pressable>
                  )
                })}
              </div>
            )}

            {/* Trailing Effort row (progressive disclosure) */}
            <div className="border-t border-border">
              {effortsDisabled ? (
                <Tooltip content={effortDisabledReason} className="w-full">
                  <Pressable aria-disabled="true" aria-label="Effort">
                    <span className="font-medium">Effort</span>
                    <span className="text-xs text-muted-foreground">Default</span>
                  </Pressable>
                </Tooltip>
              ) : (
                <>
                  <Pressable
                    aria-label="Effort"
                    aria-expanded={effortOpen}
                    onClick={() => setEffortOpen((v) => !v)}
                  >
                    <span className="font-medium">Effort</span>
                    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                      <span>{effortDisplay}</span>
                      <ChevronRight
                        aria-hidden
                        className={cn("size-3.5 transition-transform", effortOpen && "rotate-90")}
                      />
                    </span>
                  </Pressable>
                  {effortOpen ? (
                    <div
                      role="listbox"
                      aria-label="Effort options"
                      className="divide-y divide-border border-t border-border bg-muted/30"
                    >
                      {[
                        {
                          id: "",
                          name: data?.current_effort ? `Default (${data.current_effort})` : "Default",
                        },
                        ...efforts,
                      ].map((item) => {
                        const selected = effort === item.id
                        return (
                          <Pressable
                            key={item.id || "__default__"}
                            role="option"
                            aria-selected={selected}
                            className="pl-5 text-xs"
                            onClick={() => pickEffort(item.id)}
                          >
                            <span className="truncate">{item.name || item.id}</span>
                            {selected ? <Check aria-hidden className="size-3.5 shrink-0 text-primary" /> : null}
                          </Pressable>
                        )
                      })}
                    </div>
                  ) : null}
                </>
              )}
            </div>
          </>
        ) : null}
      </PopoverContent>
    </Popover>
  )
}
