import { REVIEW_ACTION_OPTIONS, RUN_MODE_OPTIONS } from "@/lib/launchConfig"
import type { ReviewAction, RunMode } from "@/lib/launchConfig"

/** Persisted Launch tab prefs (`launch.prefs.v1`). Deep-link URL params
 * override stored values at load time; the active tab's slice is saved
 * back on every change (mirrors `releasePrefs.ts`). */
export interface LaunchStartPrefs {
  runMode: RunMode
  base: string
}

export interface LaunchReviewPrefs {
  runMode: RunMode
  thenFix: boolean
  fixAction: ReviewAction
  useReviewSession: boolean
}

export interface LaunchSyncPrefs {
  merge: boolean
}

export interface LaunchPrefs {
  start: LaunchStartPrefs
  review: LaunchReviewPrefs
  sync: LaunchSyncPrefs
}

const KEY = "launch.prefs.v1"

const RUN_MODES: readonly string[] = RUN_MODE_OPTIONS.map((o) => o.value)
const FIX_ACTIONS: readonly string[] = REVIEW_ACTION_OPTIONS.map((o) => o.value)

export function loadLaunchPrefs(): LaunchPrefs {
  const fallback: LaunchPrefs = {
    start: { runMode: "preview", base: "" },
    review: { runMode: "preview", thenFix: false, fixAction: "review", useReviewSession: false },
    sync: { merge: false },
  }
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return fallback
    const p = JSON.parse(raw) as {
      start?: { runMode?: unknown; base?: unknown }
      review?: { runMode?: unknown; thenFix?: unknown; fixAction?: unknown; useReviewSession?: unknown }
      sync?: { merge?: unknown }
    }
    return {
      start: {
        runMode: typeof p.start?.runMode === "string" && RUN_MODES.includes(p.start.runMode)
          ? (p.start.runMode as RunMode)
          : "preview",
        base: typeof p.start?.base === "string" ? p.start.base : "",
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
      },
      sync: { merge: p.sync?.merge === true },
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
