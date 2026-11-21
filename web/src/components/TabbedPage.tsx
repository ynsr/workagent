import { useId, type ReactNode } from "react"
import { useSearchParams } from "react-router-dom"
import { cn } from "@/lib/utils"

export interface TabDefinition<T extends string> {
  readonly id: T
  readonly label: string
  readonly content: () => ReactNode
}

export interface TabbedPageProps<T extends string> {
  label: string
  tabs: readonly TabDefinition<T>[]
  initialTab: T
}

export function TabbedPage<T extends string>({ label, tabs, initialTab }: TabbedPageProps<T>) {
  const baseId = useId()
  const [params, setParams] = useSearchParams()
  const tabParam = params.get("tab")
  const matched = tabs.find((t) => t.id === tabParam)
  const activeTab = matched ?? tabs.find((t) => t.id === initialTab) ?? tabs[0]!

  function selectTab(next: T) {
    const nextParams = new URLSearchParams(params)
    nextParams.set("tab", next)
    setParams(nextParams, { replace: true })
  }

  return (
    <div>
      <div
        role="tablist"
        aria-label={label}
        className="mb-4 inline-flex max-w-full flex-wrap rounded-lg border p-1"
      >
        {tabs.map((t) => {
          const isSelected = activeTab.id === t.id
          const tabId = `${baseId}-tab-${t.id}`
          const panelId = `${baseId}-panel-${t.id}`
          return (
            <button
              key={t.id}
              id={tabId}
              type="button"
              role="tab"
              aria-selected={isSelected}
              aria-controls={panelId}
              onClick={() => selectTab(t.id)}
              className={cn(
                "min-h-9 shrink-0 rounded-md px-4 text-sm font-medium transition-colors",
                isSelected
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t.label}
            </button>
          )
        })}
      </div>
      <div
        id={`${baseId}-panel-${activeTab.id}`}
        role="tabpanel"
        aria-labelledby={`${baseId}-tab-${activeTab.id}`}
      >
        {activeTab.content()}
      </div>
    </div>
  )
}
