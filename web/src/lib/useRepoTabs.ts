import { useMemo } from "react"
import { useSearchParams } from "react-router-dom"
import type { Repo } from "@/lib/api"

/** An item counted by the tabs: a bare worktree path (Runs/Sessions) or a
 * status/link entry carrying the resolved main-checkout `repo` path. */
export type RepoTabItem = string | { worktree?: string; repo?: string }

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

/** Repo tabs (?repo=) shared by Dashboard/Runs/Sessions: "All" + one tab per
 * registered repo name + "(other)" when items fall outside every repo path. */
export function useRepoTabs(items: RepoTabItem[], repos: Repo[] | undefined) {
  const [params, setParams] = useSearchParams()
  const repoFilter = (params.get("repo") ?? "").trim()
  const list = repos ?? []
  const tabs = useMemo(() => {
    const names = list.map((r) => r.name)
    const counts = new Map<string, number>()
    let other = 0
    for (const item of items) {
      const k = repoKeyForItem(item, list)
      if (names.includes(k)) counts.set(k, (counts.get(k) ?? 0) + 1)
      else other += 1
    }
    return { names, counts, other }
  }, [items, list])
  function setRepo(next: string) {
    const p = new URLSearchParams(params)
    if (next) p.set("repo", next)
    else p.delete("repo")
    setParams(p, { replace: true })
  }
  return { repoFilter, setRepo, tabs }
}
