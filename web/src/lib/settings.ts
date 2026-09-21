/** localStorage-backed settings shared across the app. */

export type Theme = "light" | "dark" | "system"

export const THEME_KEY = "theme"
export const VERBOSE_KEY = "harness.verbose"
export const OPTOUT_KEY = "harness.confirm.optout"

export type OptOutAction =
  | "cleanup"
  | "sync"
  | "start"
  | "review"
  | "repo remove"
  | "link remove"

export const OPTOUT_ACTIONS: OptOutAction[] = [
  "cleanup",
  "sync",
  "start",
  "review",
  "repo remove",
  "link remove",
]

export const OPTOUT_LABELS: Record<OptOutAction, string> = {
  cleanup: "Worktree cleanup",
  sync: "Sync",
  start: "Start",
  review: "Review",
  "repo remove": "Repo removal",
  "link remove": "Link removal",
}

export function getTheme(): Theme {
  try {
    const raw = localStorage.getItem(THEME_KEY)
    if (raw === "light" || raw === "dark" || raw === "system") return raw
  } catch {
    /* storage unavailable */
  }
  return "system"
}

export function setStoredTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_KEY, theme)
  } catch {
    /* storage unavailable */
  }
}

export function getVerbose(): boolean {
  try {
    return localStorage.getItem(VERBOSE_KEY) === "1"
  } catch {
    return false
  }
}

export function setVerbose(value: boolean): void {
  try {
    localStorage.setItem(VERBOSE_KEY, value ? "1" : "0")
  } catch {
    /* storage unavailable */
  }
}

export type OptOutMap = Partial<Record<OptOutAction, boolean>>

export function getOptOuts(): OptOutMap {
  try {
    const raw = localStorage.getItem(OPTOUT_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== "object" || parsed === null) return {}
    const out: OptOutMap = {}
    for (const action of OPTOUT_ACTIONS) {
      if ((parsed as Record<string, unknown>)[action] === true) out[action] = true
    }
    return out
  } catch {
    return {}
  }
}

export function isOptedOut(action: OptOutAction): boolean {
  return getOptOuts()[action] === true
}

export function setOptOut(action: OptOutAction, value: boolean): void {
  const map = getOptOuts()
  if (value) map[action] = true
  else delete map[action]
  try {
    localStorage.setItem(OPTOUT_KEY, JSON.stringify(map))
  } catch {
    /* storage unavailable */
  }
}

export function clearOptOuts(): void {
  try {
    localStorage.removeItem(OPTOUT_KEY)
  } catch {
    /* storage unavailable */
  }
}
