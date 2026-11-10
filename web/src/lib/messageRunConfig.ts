import type { FlowStep } from "@/lib/api"

/** Scope for action/flow-template rows: worktree rows surface on the
 * Dashboard, message rows on the Messages page. Missing category means
 * worktree (matches the DB default). */
export type RowCategory = "worktree" | "message"

export const ROW_CATEGORIES: readonly RowCategory[] = ["worktree", "message"]

export function messageScoped<T extends { category?: string }>(rows: T[], scope: RowCategory = "message"): T[] {
  return rows.filter((r) => (r.category ?? "worktree") === scope)
}

export interface ActionUpsertForm {
  name: string
  label: string
  icon: string
  prompt: string
  pre: string
  post: string
  runMode: string
  runImmediately: boolean
  category: RowCategory
}

/** CLI argv (after `command: "action"`) for the add/update form.
 * run_immediately is a first-class backend column (v20). */
export function buildActionUpsertArgs(editingLabel: string | null, f: ActionUpsertForm): string[] {
  const opts = JSON.stringify({ run_mode: f.runMode })
  const flag = f.runImmediately ? "--run-immediately" : "--no-run-immediately"
  if (editingLabel !== null) {
    return [
      "update", editingLabel,
      "--name", f.name.trim(),
      ...(f.label !== editingLabel ? ["--new-label", f.label] : []),
      "--icon", f.icon.trim() || "Zap",
      "--prompt", f.prompt,
      "--pre-execution", f.pre,
      "--post-execution", f.post,
      "--default-options", opts,
      "--category", f.category,
      flag,
    ]
  }
  return [
    "add",
    "--name", f.name.trim(),
    "--label", f.label,
    ...(f.icon.trim() ? ["--icon", f.icon.trim()] : []),
    ...(f.prompt.trim() ? ["--prompt", f.prompt.trim()] : []),
    ...(f.pre.trim() ? ["--pre-execution", f.pre.trim()] : []),
    ...(f.post.trim() ? ["--post-execution", f.post.trim()] : []),
    "--default-options", opts,
    "--category", f.category,
    flag,
  ]
}

const RUN_MODES = ["preview", "headless", "terminal"] as const

/** Row's default run mode (default_options.run_mode, preview fallback). */
export function actionRunModeOf(row: { default_options?: unknown }): string {
  const opts = row.default_options
  const m = opts && typeof opts === "object" ? (opts as Record<string, unknown>).run_mode : undefined
  return typeof m === "string" && (RUN_MODES as readonly string[]).includes(m) ? m : "preview"
}

export interface FlowTemplateUpsertForm {
  name: string
  label: string
  steps: FlowStep[]
  pre: string
  post: string
  runImmediately: boolean
  category: RowCategory
}

/** CLI argv (after `command: "flow"`) for the template add/update form. */
export function buildFlowTemplateUpsertArgs(editingLabel: string | null, f: FlowTemplateUpsertForm): string[] {
  const steps = JSON.stringify(f.steps)
  const pre = f.pre.trim()
  const post = f.post.trim()
  const flag = f.runImmediately ? "--run-immediately" : "--no-run-immediately"
  if (editingLabel !== null) {
    return [
      "template", "update", editingLabel,
      "--name", f.name.trim(),
      ...(f.label !== editingLabel ? ["--new-label", f.label] : []),
      "--steps", steps,
      "--pre-execution", f.pre,
      "--post-execution", f.post,
      "--category", f.category,
      flag,
    ]
  }
  return [
    "template", "add",
    "--name", f.name.trim(),
    "--label", f.label,
    "--steps", steps,
    ...(pre ? ["--pre-execution", pre] : []),
    ...(post ? ["--post-execution", post] : []),
    "--category", f.category,
    flag,
  ]
}

/** True when the action row opts into immediate (confirm-only) runs. */
export function isRunImmediateAction(row: { run_immediately?: unknown }): boolean {
  return row.run_immediately === true
}

/** True when the template row opts into immediate (confirm-only) runs. */
export function isRunImmediateTemplate(row: { run_immediately?: unknown }): boolean {
  return row.run_immediately === true
}

/** CLI argv (after `command: "message"`) for an immediate action run. */
export function buildImmediateMessageActionArgs(label: string, messageId: string): string[] {
  return ["action", "run", label, messageId, "--yes"]
}

/** CLI argv (after `command: "message"`) for an immediate flow run. */
export function buildImmediateFlowArgs(label: string, messageId: string, worktreeRef: string): string[] {
  const args = ["flow", "run", label, messageId]
  const ref = worktreeRef.trim()
  if (ref) args.push("--ref", ref)
  args.push("--yes")
  return args
}

/** CLI argv (after `command: "action"`) for a worktree-scope immediate run. */
export function buildImmediateActionArgs(label: string, ref: string, runMode: string): string[] {
  const args = ["run", label, ref]
  if (runMode === "headless") args.push("--launch")
  else if (runMode === "terminal") args.push("--terminal")
  args.push("--yes")
  return args
}

/** CLI argv (after `command: "flow"`) for a worktree-scope immediate run. */
export function buildImmediateWorktreeFlowArgs(label: string, ref: string): string[] {
  return ["run", label, ref, "--yes"]
}

/** Split a free-text ref list (whitespace or commas) into refs. */
export function splitRefs(raw: string): string[] {
  return raw.split(/[\s,]+/).map((r) => r.trim()).filter(Boolean)
}
