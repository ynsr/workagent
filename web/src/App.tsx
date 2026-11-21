import { Navigate, Route, Routes, useLocation } from "react-router-dom"
import { AppLayout } from "@/components/layout/AppLayout"
import { AutomationLibrary } from "@/pages/AutomationLibrary"
import { Dashboard } from "@/pages/Dashboard"
import { Messages } from "@/pages/Messages"
import { NotFound } from "@/pages/NotFound"
import { Prodtests } from "@/pages/Prodtests"
import { ReposAndTrackers } from "@/pages/ReposAndTrackers"
import { RunDetail } from "@/pages/RunDetail"
import { RunsAndFlows } from "@/pages/RunsAndFlows"
import { SettingsHub } from "@/pages/SettingsHub"

function LegacyTabRedirect({ to, tab }: { to: string; tab: string }) {
  const { search } = useLocation()
  const next = new URLSearchParams(search)
  if (!next.has("tab")) next.set("tab", tab)
  return <Navigate to={{ pathname: to, search: `?${next.toString()}` }} replace />
}

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Dashboard />} />
        <Route path="launch" element={<Dashboard initialLaunchOpen />} />
        <Route path="messages" element={<Messages />} />
        <Route path="message-launch" element={<Messages initialLaunchOpen />} />
        <Route path="prodtests" element={<Prodtests />} />
        <Route path="prodtest-launch" element={<Prodtests initialLaunchOpen />} />
        <Route path="runs" element={<RunsAndFlows />} />
        <Route path="runs/:runId" element={<RunDetail />} />
        <Route path="flows" element={<LegacyTabRedirect to="/runs" tab="flows" />} />
        <Route path="library" element={<AutomationLibrary />} />
        <Route path="flow-templates" element={<LegacyTabRedirect to="/library" tab="templates" />} />
        <Route path="actions" element={<LegacyTabRedirect to="/library" tab="actions" />} />
        <Route path="repos" element={<ReposAndTrackers />} />
        <Route path="trackers" element={<LegacyTabRedirect to="/repos" tab="trackers" />} />
        <Route path="links" element={<LegacyTabRedirect to="/repos" tab="mappings" />} />
        <Route path="settings" element={<SettingsHub />} />
        <Route path="doctor" element={<LegacyTabRedirect to="/settings" tab="doctor" />} />
        {/* Runs own the transcripts; the removed Sessions paths keep working
            as in-app redirects (no server shim). */}
        <Route path="sessions" element={<Navigate to="/runs" replace />} />
        <Route path="sessions/:sessionId" element={<Navigate to="/runs" replace />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}
