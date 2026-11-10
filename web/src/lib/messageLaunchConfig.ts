import type { RunMode } from "@/lib/launchConfig"

/** Tabs on the Message Launch page — mirrors the agent operations
 * in MessageRowActions (Delete stays row-only on the Messages page). */
export type MessageLaunchMode = "adhoc" | "validate" | "action" | "flow"

export const MESSAGE_LAUNCH_MODES: readonly MessageLaunchMode[] = [
  "adhoc",
  "validate",
  "action",
  "flow",
]

export const MESSAGE_LAUNCH_LABELS: Record<MessageLaunchMode, string> = {
  adhoc: "Adhoc",
  validate: "Validate claimed issues",
  action: "Run action",
  flow: "Run flow",
}

/** Default prompt seeded into the validate tab's extra-instructions box. */
export const VALIDATE_CLAIMED_ISSUES_PROMPT =
  "Validate the claimed issues in the message content and generate Jira issues if issue(s) confirmed."

export interface MessageLaunchForm {
  ref: string
  /** Prompt body (adhoc tab, required) or extra validation instructions (validate tab). */
  prompt: string
  /** Selected message-category action / flow template label. */
  label: string
  /** Optional worktree override (`--ref`, wins over repos[]). */
  refOverride: string
  /** Optional repo pin for multi-repo messages (`--repo`). */
  repo: string
  runMode: RunMode
}

export const MESSAGE_LAUNCH_INITIAL: MessageLaunchForm = {
  ref: "",
  prompt: "",
  label: "",
  refOverride: "",
  repo: "",
  runMode: "headless",
}

/** Run-mode flags for the agent-spawning tabs (adhoc/validate/action).
 * The server appends `--yes`; preview emits no run flag. */
export function messageRunModeArgs(mode: RunMode): string[] {
  if (mode === "headless") return ["--launch"]
  if (mode === "terminal") return ["--terminal"]
  return []
}

/** CLI argv (after `command: "message"`) for one Message Launch tab. */
export function buildMessageArgs(
  tab: MessageLaunchMode,
  form: Partial<MessageLaunchForm>,
): string[] {
  const ref = (form.ref ?? "").trim()
  const refOverride = (form.refOverride ?? "").trim()
  const repo = (form.repo ?? "").trim()
  const extra = [
    ...(refOverride ? ["--ref", refOverride] : []),
    ...(repo ? ["--repo", repo] : []),
  ]

  switch (tab) {
    case "adhoc": {
      const args = ["adhoc", ref]
      const prompt = (form.prompt ?? "").trim()
      if (prompt) args.push("--prompt", prompt)
      args.push(...extra, ...messageRunModeArgs(form.runMode ?? "headless"), "--yes")
      return args
    }
    case "validate": {
      const args = ["validate", ref]
      const prompt = (form.prompt ?? "").trim()
      if (prompt) args.push("--prompt", prompt)
      args.push(...extra, ...messageRunModeArgs(form.runMode ?? "headless"), "--yes")
      return args
    }
    case "action": {
      const args = ["action", "run", (form.label ?? "").trim(), ref, ...extra]
      args.push(...messageRunModeArgs(form.runMode ?? "headless"), "--yes")
      return args
    }
    case "flow": {
      return ["flow", "run", (form.label ?? "").trim(), ref, ...extra, "--yes"]
    }
  }
}

/** Whether the tab's submit requirements are met (drives the Launch button).
 * Flow templates are not resolvable from a message alone — the CLI requires
 * `--ref <worktree>` for worktree-kind steps. */
export function messageLaunchValid(tab: MessageLaunchMode, form: MessageLaunchForm): boolean {
  if (!form.ref.trim()) return false
  if (tab === "adhoc" && !form.prompt.trim()) return false
  if (tab === "validate" && !form.prompt.trim()) return false
  if ((tab === "action" || tab === "flow") && !form.label.trim()) return false
  if (tab === "flow" && !form.refOverride.trim()) return false
  return true
}
