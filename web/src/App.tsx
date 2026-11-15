import { Navigate, Route, Routes } from "react-router-dom"
import { AppLayout } from "@/components/layout/AppLayout"
import { Dashboard } from "@/pages/Dashboard"
import { Launch } from "@/pages/Launch"
import { Repos } from "@/pages/Repos"
import { Trackers } from "@/pages/Trackers"
import { Actions } from "@/pages/Actions"
import { FlowTemplates } from "@/pages/FlowTemplates"
import { Flows } from "@/pages/Flows"
import { Messages } from "@/pages/Messages"
import { MessageLaunch } from "@/pages/MessageLaunch"
import { Prodtests } from "@/pages/Prodtests"
import { ProdtestLaunch } from "@/pages/ProdtestLaunch"
import { Links } from "@/pages/Links"
import { Doctor } from "@/pages/Doctor"
import { Runs } from "@/pages/Runs"
import { RunDetail } from "@/pages/RunDetail"
import { Settings } from "@/pages/Settings"
import { NotFound } from "@/pages/NotFound"

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Dashboard />} />
        <Route path="launch" element={<Launch />} />
        <Route path="repos" element={<Repos />} />
        <Route path="trackers" element={<Trackers />} />
        <Route path="actions" element={<Actions />} />
        <Route path="flows" element={<Flows />} />
        <Route path="prodtests" element={<Prodtests />} />
        <Route path="prodtest-launch" element={<ProdtestLaunch />} />
        <Route path="messages" element={<Messages />} />
        <Route path="flow-templates" element={<FlowTemplates />} />
        <Route path="message-launch" element={<MessageLaunch />} />
        <Route path="links" element={<Links />} />
        <Route path="doctor" element={<Doctor />} />
        <Route path="runs" element={<Runs />} />
        <Route path="runs/:runId" element={<RunDetail />} />
        {/* Runs own the transcripts; the removed Sessions paths keep working
            as in-app redirects (no server shim). */}
        <Route path="sessions" element={<Navigate to="/runs" replace />} />
        <Route path="sessions/:sessionId" element={<Navigate to="/runs" replace />} />
        <Route path="settings" element={<Settings />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}
