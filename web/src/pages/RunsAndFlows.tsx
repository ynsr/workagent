import { TabbedPage, type TabDefinition } from "@/components/TabbedPage"
import { Flows } from "@/pages/Flows"
import { Runs } from "@/pages/Runs"

type RunsTab = "runs" | "flows"

const TABS: readonly TabDefinition<RunsTab>[] = [
  { id: "runs", label: "Runs", content: () => <Runs /> },
  { id: "flows", label: "Flows", content: () => <Flows /> },
]

export function RunsAndFlows({ initialTab = "runs" }: { initialTab?: RunsTab } = {}) {
  return <TabbedPage label="Runs and Flows" tabs={TABS} initialTab={initialTab} />
}
