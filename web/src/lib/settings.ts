/** localStorage-backed settings shared across the app. */

export type Theme = "light" | "dark" | "system"

export const THEME_KEY = "theme"
export const VERBOSE_KEY = "workagent.verbose"
export const SHOW_WORKTREE_KEY = "workagent.showWorktree"
export const OPTOUT_KEY = "workagent.confirm.optout"
export const ISSUES_FILTER_KEY = "workagent.issuesFilter"

export type OptOutAction =
  | "cleanup"
  | "release"
  | "sync"
  | "start"
  | "review"
  | "flow"
  | "repo remove"
  | "link remove"
  | "link destroy"
  | "tracker remove"
  | "action delete"
  | "action update"
  | "flow template delete"
export const OPTOUT_ACTIONS: OptOutAction[] = [
  "cleanup",
  "release",
  "sync",
  "start",
  "review",
  "flow",
  "repo remove",
  "link remove",
  "link destroy",
  "tracker remove",
  "action delete",
  "action update",
  "flow template delete",
]

export const OPTOUT_LABELS: Record<OptOutAction, string> = {
  cleanup: "Worktree cleanup",
  release: "Release",
  sync: "Sync",
  start: "Start",
  review: "Review",
  flow: "Flow launch",
  "repo remove": "Repo removal",
  "link remove": "Link removal",
  "link destroy": "Deleted-row destroy",
  "tracker remove": "Tracker removal",
  "action delete": "Action deletion",
  "action update": "Action update",
  "flow template delete": "Flow template deletion",
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

export function getShowWorktree(): boolean {
  try {
    return localStorage.getItem(SHOW_WORKTREE_KEY) === "1"
  } catch {
    return false
  }
}

export function setShowWorktree(value: boolean): void {
  try {
    localStorage.setItem(SHOW_WORKTREE_KEY, value ? "1" : "0")
  } catch {
    /* storage unavailable */
  }
}

/** Persisted Recent-issues filter (scope/range/search/status). Partial or
 * corrupt values fall back to field defaults — a bad stored payload never
 * blanks the tab. */
export interface StoredIssuesFilter {
  scope?: { mine?: boolean; all?: boolean; createdByMe?: boolean }
  range?: string
  query?: string
  status?: string
}

export function getIssuesFilter(): StoredIssuesFilter {
  try {
    const raw = localStorage.getItem(ISSUES_FILTER_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== "object" || parsed === null) return {}
    return parsed as StoredIssuesFilter
  } catch {
    return {}
  }
}

export function setIssuesFilter(value: StoredIssuesFilter): void {
  try {
    localStorage.setItem(ISSUES_FILTER_KEY, JSON.stringify(value))
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
