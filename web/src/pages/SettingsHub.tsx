import { useSearchParams } from "react-router-dom"
import { Doctor } from "@/pages/Doctor"
import { Settings } from "@/pages/Settings"
import { cn } from "@/lib/utils"

type SettingsTab = "general" | "doctor"

const TABS: readonly { id: SettingsTab; label: string }[] = [
  { id: "general", label: "General" },
  { id: "doctor", label: "Doctor" },
]

export function SettingsHub({ initialTab = "general" }: { initialTab?: SettingsTab } = {}) {
  const [params, setParams] = useSearchParams()
  const tabParam = params.get("tab")
  const active: SettingsTab =
    tabParam === "general" || tabParam === "doctor" ? tabParam : initialTab

  function selectTab(next: SettingsTab) {
    const nextParams = new URLSearchParams(params)
    nextParams.set("tab", next)
    setParams(nextParams, { replace: true })
  }

  return (
    <div>
      <div
        role="tablist"
        aria-label="Settings"
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
      {active === "doctor" ? <Doctor /> : <Settings />}
    </div>
  )
}
