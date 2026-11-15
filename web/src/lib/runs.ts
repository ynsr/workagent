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
      return "flow"
    case "repo":
      return args[0] === "remove" ? "repo remove" : null
    case "link":
      if (args[0] === "remove") return "link remove"
      if (args[0] === "destroy-deleted") return "link destroy"
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

/** Short human summary of a run for toasts: "Sync jira:IPG-932". */
export function runLabel(command: string, args: readonly string[]): string {
  const refs = args.filter((a) => !a.startsWith("-"))
  return `${commandLabel(command)}${refs.length > 0 ? ` ${refs.join(" ")}` : ""}`
}
