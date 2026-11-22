import { TabbedPage, type TabDefinition } from "@/components/TabbedPage"
import { Actions } from "@/pages/Actions"
import { FlowTemplates } from "@/pages/FlowTemplates"
import { Schedules } from "@/pages/Schedules"

type LibraryTab = "templates" | "actions" | "schedules"

const TABS: readonly TabDefinition<LibraryTab>[] = [
  { id: "templates", label: "Flow Templates", content: () => <FlowTemplates /> },
  { id: "actions", label: "Actions", content: () => <Actions /> },
  { id: "schedules", label: "Schedules", content: () => <Schedules /> },
]

export function AutomationLibrary({ initialTab = "templates" }: { initialTab?: LibraryTab } = {}) {
  return <TabbedPage label="Automation Library" tabs={TABS} initialTab={initialTab} />
}
