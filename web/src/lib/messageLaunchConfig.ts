import type { RunMode } from "@/lib/launchConfig"

const MESSAGE_ID_RE = /^[a-z0-9]{26}$/

/** Bare id (or the trailing id of a Mattermost `/pl/` permalink, tolerating
 * query/fragment/trailing slash); "" when the ref carries no valid id.
 * Mirrors `extract_message_id` in `src/workagent/store_messages.py` — keep
 * the two in sync when changing permalink rules. */
export function extractMessageId(ref: string): string {
  const raw = (ref ?? "").trim()
  if (!raw) return ""
  if (MESSAGE_ID_RE.test(raw)) return raw
  const at = raw.indexOf("/pl/")
  if (at < 0) return ""
  const tail = raw.slice(at + 4).split("?", 1)[0]?.split("#", 1)[0]?.replace(/\/+$/g, "") ?? ""
  const parts = tail.split("/")
  const cand = tail.includes("/") ? (parts[parts.length - 1] ?? "") : tail
  return MESSAGE_ID_RE.test(cand) ? cand : ""
}

/** True when the ref resolves to an already-registered message (exact id,
 * permalink tail, or registered thread) — empty means auto-register via `mm`. */
export function isRegisteredMessageRef(ref: string, registeredIds: Iterable<string>): boolean {
  const mid = extractMessageId(ref)
  const set = new Set(registeredIds)
  if (mid && set.has(mid)) return true
  return set.has((ref ?? "").trim())
}


/** Tabs on the Message Launch page — mirrors the agent operations
 * in MessageRowActions (Delete stays row-only on the Messages page). */
export type MessageLaunchMode = "adhoc" | "validate" | "draft-reply" | "action" | "flow"

export const MESSAGE_LAUNCH_MODES: readonly MessageLaunchMode[] = [
  "adhoc",
  "validate",
  "draft-reply",
  "action",
  "flow",
]

export const MESSAGE_LAUNCH_LABELS: Record<MessageLaunchMode, string> = {
  adhoc: "Adhoc",
  validate: "Validate claimed issues",
  "draft-reply": "Draft reply",
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
  /** Extra instructions forwarded as --extra-prompt (draft-reply tab). */
  extraPrompt: string
  /** Selected message-category action / flow template label. */
  label: string
  /** Optional worktree override (`--ref`, wins over the linked repos). */
  refOverride: string
  /** Optional single repo pin for multi-repo messages (`--repo`). */
  repo: string
  runMode: RunMode
  harness: string
}

export const MESSAGE_LAUNCH_INITIAL: MessageLaunchForm = {
  ref: "",
  prompt: "",
  extraPrompt: "",
  label: "",
  refOverride: "",
  repo: "",
  runMode: "headless",
  harness: "",
}

/** Run-mode flags for the agent-spawning tabs (adhoc/validate/action).
 * The server appends `--yes`; preview emits no run flag. */
export function messageRunModeArgs(mode: RunMode): string[] {
  if (mode === "headless") return ["--launch"]
  if (mode === "terminal") return ["--terminal"]
  return []
}

/** Extra argv shared by every tab: worktree override, single `--repo` pin, and `--harness`. */
export function messageScopeArgs(form: Partial<MessageLaunchForm>): string[] {
  const refOverride = (form.refOverride ?? "").trim()
  const repo = (form.repo ?? "").trim()
  const harness = (form.harness ?? "").trim()
  return [
    ...(refOverride ? ["--ref", refOverride] : []),
    ...(repo ? ["--repo", repo] : []),
    ...(harness ? ["--harness", harness] : []),
  ]
}

/** CLI argv (after `command: "message"`) for one Message Launch tab. */
export function buildMessageArgs(
  tab: MessageLaunchMode,
  form: Partial<MessageLaunchForm>,
): string[] {
  const ref = (form.ref ?? "").trim()
  const extra = messageScopeArgs(form)

  switch (tab) {
    case "adhoc": {
      const args = ["adhoc", ref]
      const prompt = (form.prompt ?? "").trim()
      if (prompt) args.push("--prompt", prompt)
      args.push(...extra, ...messageRunModeArgs(form.runMode ?? "headless"), "--yes")
      return args
    }
    case "draft-reply": {
      const args = ["draft-reply", ref]
      const extraPrompt = (form.extraPrompt ?? "").trim()
      if (extraPrompt) args.push("--extra-prompt", extraPrompt)
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
