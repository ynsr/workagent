import { useState } from "react"
import { ChevronDown, GripVertical, Plus, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { RelatedReposSelect } from "@/components/RelatedReposSelect"
import { SearchableSelect } from "@/components/SearchableSelect"
import { cn } from "@/lib/utils"
import type { FlowStep } from "@/lib/api"
import { useRepos } from "@/lib/queries"

export const STEP_KINDS = [
  "start",
  "review",
  "fix_comments",
  "sync",
  "action",
  "cleanup",
  "release",
] as const

export interface ChainEditorProps {
  mode: "edit" | "launch"
  steps: FlowStep[]
  onChange?: (steps: FlowStep[]) => void
  overrides?: Record<number, Record<string, unknown>>
  onOverridesChange?: (o: Record<number, Record<string, unknown>>) => void
  knownActions: { label: string; name: string }[]
}

/** Backend rule order (mirrors store_flows.validate_template): Start-first,
 * Release-last, review→cleanup needs fix_comments, action before cleanup. */
export function validateChain(steps: FlowStep[]): string[] {
  const errors: string[] = []
  if (steps.length === 0) {
    errors.push("The chain needs at least one step.")
    return errors
  }
  const kinds = steps.map((s) => s.kind)
  for (const k of kinds) {
    if (!(STEP_KINDS as readonly string[]).includes(k)) {
      errors.push(`Unknown step kind ${JSON.stringify(k)}.`)
    }
  }
  const starts = kinds.map((k, i) => (k === "start" ? i : -1)).filter((i) => i >= 0)
  if (starts.length > 1) errors.push("At most one 'start' step is allowed.")
  if (starts.length === 1 && starts[0] !== 0) errors.push("'start' must be the first step.")
  const releases = kinds.map((k, i) => (k === "release" ? i : -1)).filter((i) => i >= 0)
  if (releases.length > 1) errors.push("At most one 'release' step is allowed.")
  if (releases.length === 1 && releases[0] !== kinds.length - 1) {
    errors.push("'release' must be the last step.")
  }
  const cleanups = kinds.map((k, i) => (k === "cleanup" ? i : -1)).filter((i) => i >= 0)
  if (releases.length === 1 && cleanups.length === 0) {
    errors.push("'release' requires a prior 'cleanup' step.")
  }
  if (releases.length === 1 && cleanups.length > 0 && Math.max(...cleanups) > (releases[0] ?? -1)) {
    errors.push("'release' requires a prior 'cleanup' step.")
  }
  for (let i = 0; i < kinds.length; i++) {
    if (kinds[i] !== "review") continue
    const rest = kinds.slice(i + 1)
    if (!rest.includes("cleanup")) continue
    const span = kinds.slice(i + 1, i + 1 + rest.indexOf("cleanup"))
    if (!span.includes("fix_comments")) {
      errors.push(`Step ${i + 1}: review…cleanup requires 'fix_comments' between them.`)
    }
  }
  steps.forEach((step, i) => {
    if (step.kind !== "action") return
    if (cleanups.length > 0 && i >= Math.min(...cleanups)) {
      errors.push(`Step ${i + 1}: 'action' steps must come before 'cleanup'.`)
    }
    if (!step.action_label?.trim()) errors.push(`Step ${i + 1}: 'action' step needs an action label.`)
  })
  steps.forEach((step, i) => {
    const cfg = JSON.stringify(step.config ?? {})
    if (/then-fix-comments|then_fix_comments/.test(cfg)) {
      errors.push(`Step ${i + 1}: then-fix-comments is rejected inside flows — use an explicit 'fix_comments' step.`)
    }
    if (step.kind === "cleanup" && /hard_delete|hard-delete|no_squash|no-squash/.test(cfg)) {
      errors.push(`Step ${i + 1}: cleanup is fixed to squash + soft-delete — hard-delete/no-squash overrides are rejected.`)
    }
  })
  return errors
}

/** Effective repo for the chain's start step (override wins over template).
 * Returns "ok" when no start step exists or a repo is set, else "missing". */
export function startRepoState(
  steps: FlowStep[],
  overrides: Record<number, Record<string, unknown>>,
): "ok" | "missing" {
  const first = steps[0]
  if (!first || first.kind !== "start") return "ok"
  const eff = overrides[0]?.repo ?? (first.config ?? {}).repo
  return typeof eff === "string" && eff.trim() ? "ok" : "missing"
}

/** Per-step config editor: run_mode select + extra-flags text (+ raw JSON add-on).
 * In launch mode pass `hints` (template defaults): the form edits DELTAS —
 * empty config shows template values as placeholder hints, and re-selecting
 * the template value removes the key so only user-set keys are stored. */
function StepConfigForm({
  kind,
  config,
  onChange,
  idPrefix,
  hints,
}: {
  kind: string
  config: Record<string, unknown>
  onChange: (next: Record<string, unknown>) => void
  idPrefix: string
  hints?: Record<string, unknown>
}) {
  const [advanced, setAdvanced] = useState(false)
  const hintRunMode = typeof hints?.run_mode === "string" ? String(hints.run_mode) : "headless"
  const runMode = typeof config.run_mode === "string" ? config.run_mode : hintRunMode
  const flags = typeof config["extra-flags"] === "string" ? String(config["extra-flags"]) : ""
  const hintFlags = typeof hints?.["extra-flags"] === "string" ? String(hints["extra-flags"]) : ""
  const strList = (v: unknown): string[] => {
    if (Array.isArray(v)) return v.map(String)
    if (typeof v === "string" && v.trim()) return v.split(",").map((p) => p.trim()).filter(Boolean)
    return []
  }
  const { data: repos } = useRepos()
  const repoNames = (repos ?? []).map((r) => r.name)
  const repo = typeof config.repo === "string" ? config.repo : ""
  const hintRepo = typeof hints?.repo === "string" ? hints.repo : ""
  const related = strList(config.related_repo)
  const hintRelated = strList(hints?.related_repo)
  const extraPrompt = typeof config.extra_prompt === "string" ? String(config.extra_prompt) : ""
  const hintExtraPrompt = typeof hints?.extra_prompt === "string" ? String(hints.extra_prompt) : ""
  const newFixSession = config.new_fix_session === true
  const hintNewFixSession = hints?.new_fix_session === true
  // Chevron badge: how many advanced (collapsed) fields carry a value.
  const advancedCount = (flags ? 1 : 0) + (extraPrompt ? 1 : 0)
  // Per-kind named fields (mirrors the cli_flow _pick allowlists):
  // start → related_repo + extra_prompt; review/fix_comments/action → extra_prompt;
  // fix_comments → new_fix_session checkbox (default off: reuse review transcript).
  const showRelated = kind === "start"
  const showRepo = kind === "start"
  const showExtraPrompt = kind === "start" || kind === "review" || kind === "fix_comments" || kind === "action"
  const showNewFixSession = kind === "fix_comments"
  function patch(key: string, value: unknown) {
    const next = { ...config }
    const hint = key === "run_mode" ? hintRunMode : hints?.[key]
    if (value === "" || value === undefined || (hints && value === hint)) delete next[key]
    else next[key] = value
    onChange(next)
  }
  function patchList(next: string[]) {
    const hinted = hints && JSON.stringify(next) === JSON.stringify(hintRelated)
    if (next.length === 0 || hinted) {
      const rest = { ...config }
      delete rest.related_repo
      onChange(rest)
      return
    }
    onChange({ ...config, related_repo: next })
  }
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {showRepo ? (
        <div className="grid gap-1.5 sm:col-span-2">
          <Label htmlFor={`${idPrefix}-repo`}>
            Repository{hints ? (hintRepo ? ` (template: ${hintRepo})` : "") : " (optional)"}
          </Label>
          <SearchableSelect
            id={`${idPrefix}-repo`}
            value={repo || hintRepo}
            options={repoNames}
            onChange={(v) => patch("repo", v)}
            placeholder={hintRepo || "Select a repo… (optional)"}
            allowCustom
          />
          {!repo && !hintRepo ? (
            <p className="text-xs text-muted-foreground">
              Optional — when empty, the issue's linked repo is used (you may be asked to pick one).
            </p>
          ) : null}
        </div>
      ) : null}
      {showRelated ? (
        <div className="grid gap-1.5 sm:col-span-2">
          <Label htmlFor={`${idPrefix}-related-repos`}>
            Related repos{hintRelated.length > 0 ? ` (template: ${hintRelated.join(", ")})` : ""}
          </Label>
          <RelatedReposSelect
            idPrefix={idPrefix}
            mainRepo=""
            value={related}
            onChange={patchList}
            variant="dropdown"
          />
        </div>
      ) : null}
      {showNewFixSession ? (
        <label className="flex items-center gap-2 text-sm sm:col-span-2">
          <input
            id={`${idPrefix}-new-session`}
            type="checkbox"
            checked={newFixSession}
            onChange={(e) => patch("new_fix_session", e.target.checked || undefined)}
            className="size-4 accent-current"
          />
          Create a new session{hintNewFixSession ? " (template: on)" : " (default: reuse review transcript)"}
        </label>
      ) : null}
      <div className="sm:col-span-2">
        <button
          type="button"
          onClick={() => setAdvanced((v) => !v)}
          aria-expanded={advanced}
          className="flex items-center gap-1.5 text-[13px] text-muted-foreground hover:text-foreground"
        >
          <ChevronDown aria-hidden className={cn("size-4 transition-transform", advanced && "rotate-180")} />
          Advanced
          {advancedCount > 0 ? (
            <span className="rounded-full bg-muted px-1.5 py-0.5 font-mono text-[11px]">{advancedCount} set</span>
          ) : null}
        </button>
        {advanced ? (
          <div className="mt-2 grid gap-2">
            <div className="grid gap-1.5">
              <Label htmlFor={`${idPrefix}-flags`}>Extra flags{hintFlags ? ` (template: ${hintFlags})` : ""}</Label>
              <Input
                id={`${idPrefix}-flags`}
                value={flags}
                onChange={(e) => patch("extra-flags", e.target.value)}
                placeholder={hintFlags || "--depth 7"}
                autoComplete="off"
                spellCheck={false}
                className="font-mono text-[13px]"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor={`${idPrefix}-run-mode`}>Run mode{hints ? ` (template: ${hintRunMode})` : ""}</Label>
              <select
                id={`${idPrefix}-run-mode`}
                value={runMode}
                onChange={(e) => patch("run_mode", e.target.value)}
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring"
              >
                <option value="headless">headless</option>
                <option value="preview">preview</option>
                <option value="terminal">terminal</option>
              </select>
            </div>
            {showExtraPrompt ? (
              <div className="grid gap-1.5">
                <Label htmlFor={`${idPrefix}-prompt`}>Extra prompt{hintExtraPrompt ? " (template set)" : ""}</Label>
                <textarea
                  id={`${idPrefix}-prompt`}
                  value={extraPrompt}
                  onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => patch("extra_prompt", e.target.value)}
                  placeholder={hintExtraPrompt || "Extra instructions appended to the harness prompt…"}
                  rows={2}
                  className="flex min-h-16 w-full rounded-md border border-input bg-transparent px-3 py-2 text-[13px] shadow-xs outline-none placeholder:text-muted-foreground/60 focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
                />
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  )
}

/** Shared step-chain editor: edit mode (full CRUD + drag-reorder) or launch
 * mode (read-only chain + per-step override editors). */
export function ChainEditor({
  mode,
  steps,
  onChange,
  overrides = {},
  onOverridesChange,
  knownActions,
}: ChainEditorProps) {
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const errors = mode === "edit" ? validateChain(steps) : []

  function setStep(i: number, next: FlowStep) {
    if (!onChange) return
    onChange(steps.map((s, j) => (j === i ? next : s)))
  }
  function addStep() {
    if (!onChange) return
    onChange([...steps, { kind: "sync", config: {} }])
  }
  function removeStep(i: number) {
    if (!onChange) return
    onChange(steps.filter((_, j) => j !== i))
  }
  function moveStep(from: number, to: number) {
    if (!onChange || from === to) return
    const next = [...steps]
    const [moved] = next.splice(from, 1)
    if (!moved) return
    next.splice(to, 0, moved)
    onChange(next)
  }
  function setOverride(i: number, next: Record<string, unknown>) {
    if (!onOverridesChange) return
    if (Object.keys(next).length === 0) {
      const { [i]: _dropped, ...rest } = overrides
      onOverridesChange(rest)
      return
    }
    onOverridesChange({ ...overrides, [i]: next })
  }

  return (
    <div className="grid gap-3">
      {steps.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {mode === "edit" ? "No steps yet — add the first step below." : "This template has no steps."}
        </p>
      ) : (
        <ol className="grid gap-3">
          {steps.map((step, i) => (
            <li
              key={i}
              draggable={mode === "edit"}
              onDragStart={mode === "edit" ? () => setDragIndex(i) : undefined}
              onDragOver={mode === "edit" ? (e) => e.preventDefault() : undefined}
              onDrop={mode === "edit" ? (e) => {
                e.preventDefault()
                if (dragIndex !== null) moveStep(dragIndex, i)
                setDragIndex(null)
              } : undefined}
              className="rounded-md border p-3"
            >
              <div className="mb-2 flex items-center gap-2">
                {mode === "edit" ? (
                  <span
                    className="cursor-grab text-muted-foreground"
                    title="Drag to reorder"
                    aria-label={`Reorder step ${i + 1}`}
                  >
                    <GripVertical aria-hidden className="size-4" />
                  </span>
                ) : null}
                <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-muted font-mono text-xs">
                  {i + 1}
                </span>
                {mode === "edit" ? (
                  <select
                    value={step.kind}
                    onChange={(e) => setStep(i, { ...step, kind: e.target.value })}
                    aria-label={`Step ${i + 1} kind`}
                    className="flex h-9 min-w-0 flex-1 rounded-md border border-input bg-transparent px-2 font-mono text-[13px] shadow-xs outline-none focus-visible:border-ring"
                  >
                    {STEP_KINDS.map((k) => (
                      <option key={k} value={k}>{k}</option>
                    ))}
                  </select>
                ) : (
                  <span className="min-w-0 flex-1 font-mono text-[13px] font-medium">
                    {step.kind}
                    {step.action_label ? (
                      <span className="text-muted-foreground"> · {step.action_label}</span>
                    ) : null}
                  </span>
                )}
                {mode === "edit" ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    title={`Remove step ${i + 1}`}
                    onClick={() => removeStep(i)}
                  >
                    <Trash2 aria-hidden />
                  </Button>
                ) : null}
              </div>
              {mode === "edit" && step.kind === "action" ? (
                <div className="mb-2 grid gap-1.5">
                  <Label htmlFor={`chain-step-${i}-action`}>Action</Label>
                  <select
                    id={`chain-step-${i}-action`}
                    value={step.action_label ?? ""}
                    onChange={(e) => setStep(i, { ...step, action_label: e.target.value })}
                    className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring"
                  >
                    <option value="">Select an action…</option>
                    {knownActions.map((a) => (
                      <option key={a.label} value={a.label}>{a.name} ({a.label})</option>
                    ))}
                  </select>
                </div>
              ) : null}
              {mode === "edit" ? (
                <StepConfigForm
                  kind={step.kind}
                  config={step.config ?? {}}
                  onChange={(config) => setStep(i, { ...step, config })}
                  idPrefix={`chain-step-${i}`}
                />
              ) : (
                <StepConfigForm
                  kind={step.kind}
                  config={overrides[i] ?? {}}
                  hints={step.config ?? {}}
                  onChange={(next) => setOverride(i, next)}
                  idPrefix={`chain-override-${i}`}
                />
              )}
            </li>
          ))}
        </ol>
      )}
      {mode === "edit" ? (
        <div>
          <Button variant="outline" size="sm" onClick={addStep}>
            <Plus aria-hidden /> Add step
          </Button>
          {errors.length > 0 ? (
            <ul className="mt-2 space-y-1" role="alert">
              {errors.map((e) => (
                <li key={e} className="text-[13px] text-destructive">{e}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
