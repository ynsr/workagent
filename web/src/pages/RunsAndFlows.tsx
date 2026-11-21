import { useSearchParams } from "react-router-dom"
import { Flows } from "@/pages/Flows"
import { Runs } from "@/pages/Runs"
import { cn } from "@/lib/utils"

type RunsTab = "runs" | "flows"

const TABS: readonly { id: RunsTab; label: string }[] = [
  { id: "runs", label: "Runs" },
  { id: "flows", label: "Flows" },
]

export function RunsAndFlows({ initialTab = "runs" }: { initialTab?: RunsTab } = {}) {
  const [params, setParams] = useSearchParams()
  const tabParam = params.get("tab")
  const active: RunsTab =
    tabParam === "runs" || tabParam === "flows" ? tabParam : initialTab

  function selectTab(next: RunsTab) {
    const nextParams = new URLSearchParams(params)
    nextParams.set("tab", next)
    setParams(nextParams, { replace: true })
  }

  return (
    <div>
      <div
        role="tablist"
        aria-label="Runs and Flows"
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
      {active === "flows" ? <Flows /> : <Runs />}
    </div>
  )
}
