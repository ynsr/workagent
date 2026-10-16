/** Persisted release prefs: checks gate + diff mode (`release.prefs.v1`). */
export type Checks = "ci" | "local" | "both" | "none"
export type DiffMode = "remote" | "local"

const KEY = "release.prefs.v1"

const CHECKS: Checks[] = ["ci", "local", "both", "none"]
const DIFFS: DiffMode[] = ["remote", "local"]

export function loadPrefs(): { checks: Checks; diff: DiffMode } {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return { checks: "ci", diff: "remote" }
    const p = JSON.parse(raw) as Partial<{ checks: string; diff: string }>
    // `none` is a one-shot run choice: always reset to the default on load.
    const checks: Checks = CHECKS.includes(p.checks as Checks) && p.checks !== "none"
      ? (p.checks as Checks)
      : "ci"
    const diff: DiffMode = p.diff === "local" ? "local" : "remote"
    return { checks, diff }
  } catch {
    return { checks: "ci", diff: "remote" }
  }
}

export function savePrefs(checks: Checks, diff: DiffMode): void {
  try {
    // Never persist `none`: reset to the default immediately.
    localStorage.setItem(KEY, JSON.stringify({ checks: checks === "none" ? "ci" : checks, diff }))
  } catch {
    /* storage unavailable */
  }
}

export const CHECK_OPTIONS: Checks[] = ["ci", "local", "both", "none"]
export const DIFF_OPTIONS: DiffMode[] = DIFFS
