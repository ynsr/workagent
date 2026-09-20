import type { OptOutAction } from "./settings"

/** Commands the API contract requires `confirm: true` for. */
export const DESTRUCTIVE_COMMANDS: readonly string[] = [
  "cleanup",
  "sync",
  "start",
  "review",
]

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
    case "repo":
      return args[0] === "remove" ? "repo remove" : null
    case "link":
      return args[0] === "remove" ? "link remove" : null
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
