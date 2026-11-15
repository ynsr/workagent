import type { OptOutAction } from "./settings"

/** Mirror of backend `_needs_confirm` (web_args.py) minus the
 * server-only guards: destructive whole commands (start/review/sync/
 * release/cleanup/adhoc) plus subcommand-gated ones (action/flow
 * run+delete, backup import, message remove/adhoc/validate/action+flow
 * run, link destroy-deleted). `--dry-run` previews never need confirm. */
export function needsConfirm(command: string, args: readonly string[]): boolean {
  const rest = args[0] === "-v" || args[0] === "--verbose" ? args.slice(1) : [...args]
  if (rest.includes("--dry-run")) return false
  switch (command) {
    case "start":
    case "review":
    case "sync":
    case "release":
    case "cleanup":
    case "adhoc":
      return true
    case "prodtest":
      return rest[0] === "plan" || rest[0] === "run"
    case "action":
    case "flow":
      if (rest[0] === "template") return rest[1] === "delete" || rest[1] === "run"
      return rest[0] === "run" || rest[0] === "delete"
    case "message":
      if (rest[0] === "action" || rest[0] === "flow") return rest[1] === "run"
      return rest[0] === "remove" || rest[0] === "adhoc" || rest[0] === "validate"
    case "backup":
      return rest[0] === "import"
    case "link":
      return rest[0] === "destroy-deleted"
    default:
      return false
  }
}

/** Which confirmation opt-out bucket a (command, subcommand) pair belongs to. */
export function commandAction(
  command: string,
  args: readonly string[],
): OptOutAction | null {
  const rest = args[0] === "-v" || args[0] === "--verbose" ? args.slice(1) : args
  switch (command) {
    case "cleanup":
      return "cleanup"
    case "sync":
      return "sync"
    case "start":
      return "start"
    case "review":
      return "review"
    case "prodtest":
      return "prodtest"
    case "flow":
      return rest[0] === "template" && rest[1] === "delete" ? "flow template delete" : "flow"
    case "repo":
      return rest[0] === "remove" ? "repo remove" : null
    case "link":
      if (rest[0] === "remove") return "link remove"
      if (rest[0] === "destroy-deleted") return "link destroy"
      return null
    default:
      return null
  }
}

export function commandLabel(command: string): string {
  switch (command) {
    case "start":
      return "Start"
    case "review":
      return "Review"
    case "cleanup":
      return "Cleanup"
    case "sync":
      return "Sync"
    case "prodtest":
      return "Prodtest"
    case "flow":
      return "Flow"
    case "register":
      return "Register"
    case "repo":
      return "Repo"
    case "link":
      return "Link"
    default:
      return command
  }
}

/** session_type → the label shown for a run's transcript. */
const SESSION_TYPE_LABELS: Record<string, string> = {
  start: "Start (task)",
  review: "Review",
  sync: "Sync",
  fix_comments: "Fix comments",
  custom_action: "Custom action",
  flow: "Flow",
  prodtest_plan: "Prodtest plan",
  prodtest_run: "Prodtest run",
}

/** Kind label for a run's transcript. The stored `session_type` wins: a
 * fix-comments launch stores `command='review'` with the flags dropped, so it
 * is only distinguishable there. Pre-migration rows (null) fall back to the
 * initiator command + args, mirroring the backend derivation. Unknown → the
 * command itself. */
export function runKind(
  command: string,
  args: readonly string[] = [],
  sessionType?: string | null,
): string {
  const key = (sessionType ?? "").trim().toLowerCase()
  const stored = Object.hasOwn(SESSION_TYPE_LABELS, key)
    ? SESSION_TYPE_LABELS[key]
    : undefined
  if (stored) return stored
  const c = command.trim().toLowerCase()
  if (c === "prodtest") {
    // The web launches prodtest split: command="prodtest", args[0]=plan|run.
    const sub = (args[0] ?? "").trim().toLowerCase()
    if (sub === "plan") return "Prodtest plan"
    if (sub === "run") return "Prodtest run"
  }
  if (c === "review" && args.includes("--fix-comments")) return "Fix comments"
  switch (c) {
    case "start":
      return "Start (task)"
    case "review":
      return "Review"
    case "sync":
      return "Sync"
    case "flow":
      return "Flow"
    case "action":
    case "adhoc":
      return "Custom action"
  }
  const joined = c.split(/\s+/).slice(0, 2).join(" ")
  if (joined === "prodtest plan") return "Prodtest plan"
  if (joined === "prodtest run") return "Prodtest run"
  return command || "—"
}

/** Short human summary of a run for toasts: "Sync jira:IPG-932". */
export function runLabel(command: string, args: readonly string[]): string {
  const refs = args.filter((a) => !a.startsWith("-"))
  return `${commandLabel(command)}${refs.length > 0 ? ` ${refs.join(" ")}` : ""}`
}
