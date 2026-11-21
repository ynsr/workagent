import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ProdtestPlanViewer } from "./ProdtestPlanViewer"
import { api } from "@/lib/api"

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

describe("ProdtestPlanViewer", () => {
  it("renders structured cases and switches to syntax-highlighted YAML view", async () => {
    vi.spyOn(api, "prodtestPlan").mockResolvedValue({
      issue_key: "IPG-42",
      status: "pending",
      plan_path: "/repo/.workagent/prodtest/plans/IPG-42.yaml",
      plan_revision: 3,
      plan_risk: "medium",
      plan_diff_hash: "sha256:abc",
      release_sha: "",
      last_run_id: "",
      updated_at: "2026-11-22T00:00:00Z",
      truncated: false,
      yaml: "issue: IPG-42\nrevision: 3\ncases:\n  - id: TC-1\n    title: Happy path\n",
      parse_error: null,
      summary: "Verify checkout endpoint",
      preconditions: [{ id: "PRE-1", title: "Service healthy" }],
      cases: [
        {
          id: "TC-1",
          title: "Happy path",
          kind: "smoke",
          priority: "must",
          safety: "read-only",
        },
      ],
      metadata: {},
    })

    const user = userEvent.setup()
    wrap(<ProdtestPlanViewer issueKey="IPG-42" />)

    expect(await screen.findByText("Verify checkout endpoint")).toBeInTheDocument()
    expect(screen.getByText("PRE-1")).toBeInTheDocument()
    expect(screen.getByText("Happy path")).toBeInTheDocument()
    expect(screen.getByText("rev3")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "YAML" }))
    expect(screen.getByText("revision")).toBeInTheDocument()
  })
})
