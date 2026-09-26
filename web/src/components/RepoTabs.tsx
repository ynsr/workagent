import { useRepoTabs } from "@/lib/useRepoTabs"

type RepoTabs = ReturnType<typeof useRepoTabs>

/** "All repos" + per-repo pill tabs (?repo=), with an "(other)" bucket.
 * Shared by Runs/Sessions (StatusTable keeps its own count-in-label variant). */
export function RepoTabsRow({ repoTabs }: { repoTabs: RepoTabs }) {
  const btn = (active: boolean) =>
    active
      ? "min-h-11 rounded-full bg-primary px-3.5 text-sm font-medium text-primary-foreground"
      : "min-h-11 rounded-full border px-3.5 text-sm text-muted-foreground hover:text-foreground"
  return (
    <div role="group" aria-label="Filter by repo" className="mb-4 flex flex-wrap gap-1.5">
      <button type="button" aria-pressed={!repoTabs.repoFilter} onClick={() => repoTabs.setRepo("")} className={btn(!repoTabs.repoFilter)}>
        All repos
      </button>
      {repoTabs.tabs.names.map((n) => (
        <button type="button" key={n} aria-pressed={repoTabs.repoFilter === n} onClick={() => repoTabs.setRepo(n)} className={btn(repoTabs.repoFilter === n)}>
          {n} ({repoTabs.tabs.counts.get(n) ?? 0})
        </button>
      ))}
      {repoTabs.tabs.other > 0 ? (
        <button type="button" aria-pressed={repoTabs.repoFilter === "(other)"} onClick={() => repoTabs.setRepo("(other)")} className={btn(repoTabs.repoFilter === "(other)")}>
          (other) ({repoTabs.tabs.other})
        </button>
      ) : null}
    </div>
  )
}
