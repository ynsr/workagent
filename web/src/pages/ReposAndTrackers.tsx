import { TabbedPage, type TabDefinition } from "@/components/TabbedPage"
import { Links } from "@/pages/Links"
import { Repos } from "@/pages/Repos"
import { Trackers } from "@/pages/Trackers"

type ReposTab = "repos" | "trackers" | "mappings"

const TABS: readonly TabDefinition<ReposTab>[] = [
  { id: "repos", label: "Repos", content: () => <Repos /> },
  { id: "trackers", label: "Trackers", content: () => <Trackers /> },
  { id: "mappings", label: "Mappings", content: () => <Links /> },
]

export function ReposAndTrackers({ initialTab = "repos" }: { initialTab?: ReposTab } = {}) {
  return <TabbedPage label="Repos and Trackers" tabs={TABS} initialTab={initialTab} />
}
