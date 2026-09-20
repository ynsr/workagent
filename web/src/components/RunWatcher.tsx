import { useEffect, useRef } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { invalidateAfterRun, useRuns } from "@/lib/queries"
import { runLabel } from "@/lib/runs"
import type { RunState } from "@/lib/api"

function stateToast(
  state: RunState,
  label: string,
): { message: string; kind: "success" | "error" | "warning" | "info" } {
  switch (state) {
    case "succeeded":
      return { message: `${label} — succeeded`, kind: "success" }
    case "failed":
      return { message: `${label} — failed (open the run for output)`, kind: "error" }
    case "needs_input":
      return { message: `${label} — needs input`, kind: "warning" }
    case "cancelled":
      return { message: `${label} — cancelled`, kind: "info" }
    case "running":
      return { message: `${label} — running`, kind: "info" }
  }
}

/**
 * Polls the run list; when a run leaves `running`, invalidates the read
 * queries it may have changed (status/repos/links/doctor — the contract
 * requires a status refresh after any run finishes) and toasts the outcome.
 * Mounted once in the layout.
 */
export function RunWatcher() {
  const qc = useQueryClient()
  const { data } = useRuns()
  const prevStatesRef = useRef<Map<string, RunState> | null>(null)

  useEffect(() => {
    if (!data) return
    const prev = prevStatesRef.current
    prevStatesRef.current = new Map(data.map((r) => [r.id, r.state]))
    if (!prev) return
    const finished = data.find(
      (r) => prev.get(r.id) === "running" && r.state !== "running",
    )
    if (!finished) return
    invalidateAfterRun(qc)
    const { message, kind } = stateToast(
      finished.state,
      runLabel(finished.command, finished.args),
    )
    toast[kind](message)
  }, [data, qc])

  return null
}
