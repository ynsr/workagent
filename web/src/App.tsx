import { Route, Routes } from "react-router-dom"
import { AppLayout } from "@/components/layout/AppLayout"
import { Dashboard } from "@/pages/Dashboard"
import { Launch } from "@/pages/Launch"
import { Repos } from "@/pages/Repos"
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
        <Route path="links" element={<Links />} />
        <Route path="doctor" element={<Doctor />} />
        <Route path="runs" element={<Runs />} />
        <Route path="runs/:runId" element={<RunDetail />} />
        <Route path="settings" element={<Settings />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}
