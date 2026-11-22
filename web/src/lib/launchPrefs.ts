import { PRODTEST_ACTION_OPTIONS, REVIEW_ACTION_OPTIONS, RUN_MODE_OPTIONS, START_ACTION_OPTIONS } from "@/lib/launchConfig"
import type { ProdtestAction, ReviewAction, RunMode, StartAction } from "@/lib/launchConfig"

/** Persisted Launch tab prefs (`launch.prefs.v1`). Deep-link URL params
 * override stored values at load time; the active tab's slice is saved
 * back on every change (mirrors `releasePrefs.ts`).
 *
 * Start-tab base/action/run-mode additionally stick per repo under
 * `launch.startByRepo.v1`: picking a repo reloads that repo's last Start
 * values; legacy `start.*` fields stay the fallback for repos never saved. */
export interface LaunchStartPrefs {
  runMode: RunMode
  startAction: StartAction
  base: string
  harness: string
  model: string
  effort: string
}

/** Per-repo Start slice (issue #114): base branch, Implement/Plan action,
 * and run mode stick to the repo chosen in the Start form. */
export interface LaunchStartRepoPrefs {
  runMode: RunMode
  startAction: StartAction
  base: string
}

export interface LaunchReviewPrefs {
  runMode: RunMode
  thenFix: boolean
  fixAction: ReviewAction
  useReviewSession: boolean
  harness: string
  model: string
  effort: string
}

export interface LaunchProdtestPrefs {
  runMode: RunMode
  prodtestAction: ProdtestAction
  harness: string
  model: string
  effort: string
}

export interface LaunchSyncPrefs {
  merge: boolean
  harness: string
  model: string
  effort: string
}

export interface LaunchAdhocPrefs {
  runMode: RunMode
  harness: string
  model: string
  effort: string
}

export interface LaunchPrefs {
  start: LaunchStartPrefs
  review: LaunchReviewPrefs
  prodtest: LaunchProdtestPrefs
  sync: LaunchSyncPrefs
  adhoc: LaunchAdhocPrefs
}
const KEY = "launch.prefs.v1"
const START_BY_REPO_KEY = "launch.startByRepo.v1"

const RUN_MODES: readonly string[] = RUN_MODE_OPTIONS.map((o) => o.value)
const FIX_ACTIONS: readonly string[] = REVIEW_ACTION_OPTIONS.map((o) => o.value)
const PRODTEST_ACTIONS: readonly string[] = PRODTEST_ACTION_OPTIONS.map((o) => o.value)
const START_ACTIONS: readonly string[] = START_ACTION_OPTIONS.map((o) => o.value)

function cleanStartRepoEntry(raw: unknown): LaunchStartRepoPrefs | null {
  if (typeof raw !== "object" || raw === null) return null
  const r = raw as { runMode?: unknown; startAction?: unknown; base?: unknown }
  if (typeof r.runMode !== "string" || !RUN_MODES.includes(r.runMode)) return null
  if (typeof r.startAction !== "string" || !START_ACTIONS.includes(r.startAction)) return null
  if (typeof r.base !== "string") return null
  return { runMode: r.runMode as RunMode, startAction: r.startAction as StartAction, base: r.base }
}

/** Last-used Start base/action/run-mode per repo name. */
export function loadStartPrefsByRepo(): Record<string, LaunchStartRepoPrefs> {
  try {
    const raw = localStorage.getItem(START_BY_REPO_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as Record<string, unknown>
    if (typeof parsed !== "object" || parsed === null) return {}
    const out: Record<string, LaunchStartRepoPrefs> = {}
    for (const [repo, entry] of Object.entries(parsed)) {
      const clean = cleanStartRepoEntry(entry)
      if (repo.trim() && clean) out[repo] = clean
    }
    return out
  } catch {
    return {}
  }
}

export function saveStartPrefsForRepo(repo: string, prefs: LaunchStartRepoPrefs): void {
  const name = repo.trim()
  if (!name) return
  try {
    const all = loadStartPrefsByRepo()
    all[name] = prefs
    localStorage.setItem(START_BY_REPO_KEY, JSON.stringify(all))
  } catch {
    /* storage unavailable */
  }
}

export function loadLaunchPrefs(): LaunchPrefs {
  const fallback: LaunchPrefs = {
    start: { runMode: "preview", startAction: "implement", base: "", harness: "", model: "", effort: "" },
    review: { runMode: "preview", thenFix: false, fixAction: "review", useReviewSession: false, harness: "", model: "", effort: "" },
    prodtest: { runMode: "preview", prodtestAction: "run", harness: "", model: "", effort: "" },
    sync: { merge: false, harness: "", model: "", effort: "" },
    adhoc: { runMode: "headless", harness: "", model: "", effort: "" },
  }
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return fallback
    const p = JSON.parse(raw) as {
      start?: { runMode?: unknown; startAction?: unknown; base?: unknown; harness?: unknown; model?: unknown; effort?: unknown }
      review?: { runMode?: unknown; thenFix?: unknown; fixAction?: unknown; useReviewSession?: unknown; harness?: unknown; model?: unknown; effort?: unknown }
      prodtest?: { runMode?: unknown; prodtestAction?: unknown; harness?: unknown; model?: unknown; effort?: unknown }
      sync?: { merge?: unknown; harness?: unknown; model?: unknown; effort?: unknown }
      adhoc?: { runMode?: unknown; harness?: unknown; model?: unknown; effort?: unknown }
    }
    return {
      start: {
        runMode: typeof p.start?.runMode === "string" && RUN_MODES.includes(p.start.runMode)
          ? (p.start.runMode as RunMode)
          : "preview",
        startAction: typeof p.start?.startAction === "string" && START_ACTIONS.includes(p.start.startAction)
          ? (p.start.startAction as StartAction)
          : "implement",
        base: typeof p.start?.base === "string" ? p.start.base : "",
        harness: typeof p.start?.harness === "string" ? p.start.harness : "",
        model: typeof p.start?.model === "string" ? p.start.model : "",
        effort: typeof p.start?.effort === "string" ? p.start.effort : "",
      },
      review: {
        runMode: typeof p.review?.runMode === "string" && RUN_MODES.includes(p.review.runMode)
          ? (p.review.runMode as RunMode)
          : "preview",
        thenFix: p.review?.thenFix === true,
        fixAction: typeof p.review?.fixAction === "string" && FIX_ACTIONS.includes(p.review.fixAction)
          ? (p.review.fixAction as ReviewAction)
          : "review",
        useReviewSession: p.review?.useReviewSession === true,
        harness: typeof p.review?.harness === "string" ? p.review.harness : "",
        model: typeof p.review?.model === "string" ? p.review.model : "",
        effort: typeof p.review?.effort === "string" ? p.review.effort : "",
      },
      prodtest: {
        runMode: typeof p.prodtest?.runMode === "string" && RUN_MODES.includes(p.prodtest.runMode)
          ? (p.prodtest.runMode as RunMode)
          : "preview",
        prodtestAction: typeof p.prodtest?.prodtestAction === "string" && PRODTEST_ACTIONS.includes(p.prodtest.prodtestAction)
          ? (p.prodtest.prodtestAction as ProdtestAction)
          : "run",
        harness: typeof p.prodtest?.harness === "string" ? p.prodtest.harness : "",
        model: typeof p.prodtest?.model === "string" ? p.prodtest.model : "",
        effort: typeof p.prodtest?.effort === "string" ? p.prodtest.effort : "",
      },
      sync: {
        merge: p.sync?.merge === true,
        harness: typeof p.sync?.harness === "string" ? p.sync.harness : "",
        model: typeof p.sync?.model === "string" ? p.sync.model : "",
        effort: typeof p.sync?.effort === "string" ? p.sync.effort : "",
      },
      adhoc: {
        runMode: typeof p.adhoc?.runMode === "string" && RUN_MODES.includes(p.adhoc.runMode)
          ? (p.adhoc.runMode as RunMode)
          : "headless",
        harness: typeof p.adhoc?.harness === "string" ? p.adhoc.harness : "",
        model: typeof p.adhoc?.model === "string" ? p.adhoc.model : "",
        effort: typeof p.adhoc?.effort === "string" ? p.adhoc.effort : "",
      },
    }
  } catch {
    return fallback
  }
}

export function saveLaunchPrefs(p: LaunchPrefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(p))
  } catch {
    /* storage unavailable */
  }
}
