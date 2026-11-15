import { describe, expect, it } from "vitest"
import { EMPTY_WORKTREE_FILTERS, filterWorktreeKeys, worktreeMatchesFilter } from "@/lib/worktreeFilter"
import type { WorktreeEntry, WorktreeMap } from "@/lib/api"

const ROWS: WorktreeMap = {
  "jira:AAA-1": { branch: "feat/a", repo: "/r/proj-a", worktree: "/wt/a" } as WorktreeEntry,
  "jira:BBB-2": { branch: "feat/b", repo: "/r/proj-b", worktree: "/wt/b" } as WorktreeEntry,
}

function row(key: string): WorktreeEntry {
  const entry = ROWS[key]
  if (!entry) throw new Error(`missing fixture row ${key}`)
  return entry
}

describe("worktreeMatchesFilter", () => {
  it("matches free text on key/branch", () => {
    expect(worktreeMatchesFilter("jira:AAA-1", row("jira:AAA-1"), { ...EMPTY_WORKTREE_FILTERS, q: "feat/a" })).toBe(true)
    expect(worktreeMatchesFilter("jira:AAA-1", row("jira:AAA-1"), { ...EMPTY_WORKTREE_FILTERS, q: "zzz" })).toBe(false)
  })

  it("matches repo display names via the fallback", () => {
    const repoDisplayOf = (entry: WorktreeEntry) => (entry.repo === "/r/proj-b" ? "proj-b" : "")
    expect(
      worktreeMatchesFilter("jira:BBB-2", row("jira:BBB-2"), { ...EMPTY_WORKTREE_FILTERS, q: "proj-b" }, "", undefined, repoDisplayOf),
    ).toBe(true)
    expect(
      worktreeMatchesFilter("jira:AAA-1", row("jira:AAA-1"), { ...EMPTY_WORKTREE_FILTERS, q: "proj-b" }, "", undefined, repoDisplayOf),
    ).toBe(false)
    expect(filterWorktreeKeys(ROWS, { ...EMPTY_WORKTREE_FILTERS, q: "proj-b" }, "", undefined, repoDisplayOf)).toEqual(["jira:BBB-2"])
  })
})
