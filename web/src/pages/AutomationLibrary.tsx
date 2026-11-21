import { TabbedPage, type TabDefinition } from "@/components/TabbedPage"
import { Actions } from "@/pages/Actions"
import { FlowTemplates } from "@/pages/FlowTemplates"

type LibraryTab = "templates" | "actions"

const TABS: readonly TabDefinition<LibraryTab>[] = [
  { id: "templates", label: "Flow Templates", content: () => <FlowTemplates /> },
  { id: "actions", label: "Actions", content: () => <Actions /> },
]

export function AutomationLibrary({ initialTab = "templates" }: { initialTab?: LibraryTab } = {}) {
  return <TabbedPage label="Automation Library" tabs={TABS} initialTab={initialTab} />
}
