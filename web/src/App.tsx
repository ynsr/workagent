import { Navigate, Route, Routes } from "react-router-dom"
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
        <Route path="flows" element={<RunsAndFlows initialTab="flows" />} />
        <Route path="library" element={<AutomationLibrary />} />
        <Route path="flow-templates" element={<AutomationLibrary initialTab="templates" />} />
        <Route path="actions" element={<AutomationLibrary initialTab="actions" />} />
        <Route path="repos" element={<ReposAndTrackers />} />
        <Route path="trackers" element={<ReposAndTrackers initialTab="trackers" />} />
        <Route path="links" element={<ReposAndTrackers initialTab="mappings" />} />
        <Route path="settings" element={<SettingsHub />} />
        <Route path="doctor" element={<SettingsHub initialTab="doctor" />} />
        {/* Runs own the transcripts; the removed Sessions paths keep working
            as in-app redirects (no server shim). */}
        <Route path="sessions" element={<Navigate to="/runs" replace />} />
        <Route path="sessions/:sessionId" element={<Navigate to="/runs" replace />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}
