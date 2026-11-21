import { TabbedPage, type TabDefinition } from "@/components/TabbedPage"
import { Doctor } from "@/pages/Doctor"
import { Settings } from "@/pages/Settings"

type SettingsTab = "general" | "doctor"

const TABS: readonly TabDefinition<SettingsTab>[] = [
  { id: "general", label: "General", content: () => <Settings /> },
  { id: "doctor", label: "Doctor", content: () => <Doctor /> },
]

export function SettingsHub({ initialTab = "general" }: { initialTab?: SettingsTab } = {}) {
  return <TabbedPage label="Settings" tabs={TABS} initialTab={initialTab} />
}
