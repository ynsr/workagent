import { useSearchParams } from "react-router-dom"
import { Actions } from "@/pages/Actions"
import { FlowTemplates } from "@/pages/FlowTemplates"
import { cn } from "@/lib/utils"

type LibraryTab = "templates" | "actions"

const TABS: readonly { id: LibraryTab; label: string }[] = [
  { id: "templates", label: "Flow Templates" },
  { id: "actions", label: "Actions" },
]

export function AutomationLibrary({ initialTab = "templates" }: { initialTab?: LibraryTab } = {}) {
  const [params, setParams] = useSearchParams()
  const tabParam = params.get("tab")
  const active: LibraryTab =
    tabParam === "templates" || tabParam === "actions" ? tabParam : initialTab

  function selectTab(next: LibraryTab) {
    const nextParams = new URLSearchParams(params)
    nextParams.set("tab", next)
    setParams(nextParams, { replace: true })
  }

  return (
    <div>
      <div
        role="tablist"
        aria-label="Automation Library"
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
      {active === "actions" ? <Actions /> : <FlowTemplates />}
    </div>
  )
}
