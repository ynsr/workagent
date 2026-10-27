import { useMemo } from "react"
import { useSearchParams } from "react-router-dom"
import type { Repo } from "@/lib/api"

/** An item counted by the tabs: a bare worktree path (Runs/Sessions) or a
 * status/link entry carrying the resolved main-checkout `repo` path.
 * Sessions with a NULL worktree_ref (flow-owned rows) map to null and
 * count under "(other)". */
export type RepoTabItem = string | { worktree?: string; repo?: string } | null | undefined

function normPath(p: string): string {
  return p.replace(/\\/g, "/").replace(/\/$/, "")
}

/** Group key for one item: longest-prefix match of a worktree path against
 * registered main-checkout paths, else "(other)". */
export function repoKeyForPath(itemPath: string, repos: Repo[]): string {
  const norm = normPath(itemPath)
  let best = ""
  let bestLen = -1
  for (const r of repos) {
    const rp = normPath(r.path ?? "")
    if (rp && (norm === rp || norm.startsWith(rp + "/")) && rp.length > bestLen) {
      best = r.name
      bestLen = rp.length
    }
  }
  if (best) return best
  return "(other)"
}

/** Group key for a tab item. Status/link entries carry the resolved
 * main-checkout `repo` path — match that directly against the registered
 * repo paths (exact, path, or name). Bare paths fall back to prefix match. */
export function repoKeyForItem(item: RepoTabItem, repos: Repo[]): string {
  if (item == null) return "(other)"
  if (typeof item === "string") return repoKeyForPath(item, repos)
  const repo = (item.repo ?? "").trim()
  if (repo) {
    const norm = normPath(repo)
    for (const r of repos) {
      if (r.name === repo) return r.name
      const rp = normPath(r.path ?? "")
      if (rp && (norm === rp || norm.startsWith(rp + "/"))) return r.name
    }
  }
  return repoKeyForPath(item.worktree ?? "", repos)
}

/** Display label for a status/link entry's repo: the registered name when
 * it resolves against `repos`, else the basename of the recorded `repo`
 * path ("" when no repo recorded). Shared by StatusTable groupings and the
 * DeletedTable repo filter so both spell names the same way. */
export function repoDisplayForEntry(item: RepoTabItem, repos: Repo[]): string {
  if (item != null && typeof item !== "string") {
    const raw = (item.repo ?? "").trim()
    if (raw) {
      const key = repoKeyForItem(item, repos)
      if (key !== "(other)") return key
      const base = raw.replace(/\\/g, "/").replace(/\/$/, "").split("/").pop() ?? ""
      if (base) return base
    }
  }
  if (typeof item === "string" && item.trim()) {
    const key = repoKeyForItem(item, repos)
    if (key !== "(other)") return key
  }
  return ""
}

/** Repo tabs (?repo=) shared by Dashboard/Runs/Sessions: "All" + one tab per
 * registered repo name with at least one item + "(other)" when items fall
 * outside every repo path. A stale ?repo= value that matches no tab falls
 * back to "All" (empty filter). */
export function useRepoTabs(items: RepoTabItem[], repos: Repo[] | undefined) {
  const [params, setParams] = useSearchParams()
  const list = repos ?? []
  const tabs = useMemo(() => {
    const allNames = list.map((r) => r.name)
    const counts = new Map<string, number>()
    let other = 0
    for (const item of items) {
      const k = repoKeyForItem(item, list)
      if (allNames.includes(k)) counts.set(k, (counts.get(k) ?? 0) + 1)
      else other += 1
    }
    const names = allNames.filter((n) => (counts.get(n) ?? 0) > 0)
    return { names, counts, other }
  }, [items, list])
  const raw = (params.get("repo") ?? "").trim()
  const repoFilter =
    raw && raw !== "(other)" && !tabs.names.includes(raw) ? "" : raw
  function setRepo(next: string) {
    const p = new URLSearchParams(params)
    if (next) p.set("repo", next)
    else p.delete("repo")
    setParams(p, { replace: true })
  }
  return { repoFilter, setRepo, tabs }
}
