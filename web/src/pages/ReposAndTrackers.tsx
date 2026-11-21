import { useSearchParams } from "react-router-dom"
import { Links } from "@/pages/Links"
import { Repos } from "@/pages/Repos"
import { Trackers } from "@/pages/Trackers"
import { cn } from "@/lib/utils"

type ReposTab = "repos" | "trackers" | "mappings"

const TABS: readonly { id: ReposTab; label: string }[] = [
  { id: "repos", label: "Repos" },
  { id: "trackers", label: "Trackers" },
  { id: "mappings", label: "Mappings" },
]

export function ReposAndTrackers({ initialTab = "repos" }: { initialTab?: ReposTab } = {}) {
  const [params, setParams] = useSearchParams()
  const tabParam = params.get("tab")
  const active: ReposTab =
    tabParam === "repos" || tabParam === "trackers" || tabParam === "mappings"
      ? tabParam
      : initialTab

  function selectTab(next: ReposTab) {
    const nextParams = new URLSearchParams(params)
    nextParams.set("tab", next)
    setParams(nextParams, { replace: true })
  }

  return (
    <div>
      <div
        role="tablist"
        aria-label="Repos and Trackers"
        className="mb-4 inline-flex max-w-full flex-wrap rounded-lg border p-1"
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={active === t.id}
            onClick={() => selectTab(t.id)}
            className={cn(
              "min-h-9 shrink-0 rounded-md px-4 text-sm font-medium transition-colors",
              active === t.id
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      {active === "trackers" ? <Trackers /> : active === "mappings" ? <Links /> : <Repos />}
    </div>
  )
}
