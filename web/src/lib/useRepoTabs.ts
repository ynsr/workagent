import { useMemo } from "react"
import { useSearchParams } from "react-router-dom"
import type { Repo } from "@/lib/api"

/** Group key for one item: registered repo name whose path is a prefix of
 * the item path, else "(other)". */
export function repoKeyForPath(itemPath: string, repos: Repo[]): string {
  const norm = itemPath.replace(/\\/g, "/").replace(/\/$/, "")
  let best = ""
  let bestLen = -1
  for (const r of repos) {
    const rp = (r.path ?? "").replace(/\\/g, "/").replace(/\/$/, "")
    if (rp && (norm === rp || norm.startsWith(rp + "/")) && rp.length > bestLen) {
      best = r.name
      bestLen = rp.length
    }
  }
  if (best) return best
  return "(other)"
}

/** Repo tabs (?repo=) shared by Dashboard/Runs/Sessions: "All" + one tab per
 * registered repo name + "(other)" when items fall outside every repo path. */
export function useRepoTabs(itemPaths: string[], repos: Repo[] | undefined) {
  const [params, setParams] = useSearchParams()
  const repoFilter = (params.get("repo") ?? "").trim()
  const list = repos ?? []
  const tabs = useMemo(() => {
    const names = list.map((r) => r.name)
    const counts = new Map<string, number>()
    let other = 0
    for (const p of itemPaths) {
      const k = repoKeyForPath(p, list)
      if (names.includes(k)) counts.set(k, (counts.get(k) ?? 0) + 1)
      else other += 1
    }
    return { names, counts, other }
  }, [itemPaths, list])
  function setRepo(next: string) {
    const p = new URLSearchParams(params)
    if (next) p.set("repo", next)
    else p.delete("repo")
    setParams(p, { replace: true })
  }
  return { repoFilter, setRepo, tabs }
}
