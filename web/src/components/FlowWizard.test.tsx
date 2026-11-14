import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it, vi } from "vitest"
import { FlowWizard } from "@/components/FlowWizard"
import type { FlowTemplateRow } from "@/lib/api"

vi.mock("@/lib/queries", () => ({
  useActions: () => ({
    data: { actions: [{ label: "lint", name: "Lint" }, { label: "typecheck", name: "Typecheck" }] },
  }),
  useCreateRun: () => ({ mutateAsync: vi.fn() }),
  useRepos: () => ({ data: [] }),
}))

const TEMPLATE = {
  name: "ship",
  label: "ship",
  steps: [
    { kind: "start", config: { repo: "proj" } },
    { kind: "action", config: {} },
  ],
} as unknown as FlowTemplateRow

function renderWizard(template: FlowTemplateRow = TEMPLATE, refs: string[] = ["jira:IPG-1"]) {
  const client = new QueryClient()
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <FlowWizard template={template} refs={refs} onClose={vi.fn()} />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe("FlowWizard", () => {
  it("populates the custom-action dropdown from useActions", () => {
    renderWizard()
    expect(screen.getByRole("option", { name: "Lint (lint)" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Typecheck (typecheck)" })).toBeInTheDocument()
  })
})

describe("FlowWizard footer groups", () => {
  it("renders Save, Save as, Reset buttons alongside Run flow", () => {
    renderWizard()
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Save as" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Reset" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Run flow" })).toBeInTheDocument()
  })
})
describe("FlowWizard fan-out summary", () => {
  it("announces concurrent fan-out for all-parallel multi-worktree chains", () => {
    const template = {
      ...TEMPLATE,
      steps: [
        { kind: "start", config: { repo: "proj" } },
        { kind: "review", config: {}, execution_mode: "parallel" },
      ],
    } as unknown as FlowTemplateRow
    const { unmount } = renderWizard(template, ["a", "b"])
    expect(screen.getByText(/Runs on 2 worktrees fan out at once/)).toBeInTheDocument()
    unmount()
  })

  it("hides the fan-out summary for a single worktree", () => {
    const { unmount } = renderWizard()
    expect(screen.queryByText(/Runs on \d+ worktrees/)).not.toBeInTheDocument()
    unmount()
  })
})
