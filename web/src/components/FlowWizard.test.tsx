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

function renderWizard() {
  const client = new QueryClient()
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <FlowWizard template={TEMPLATE} refs={["jira:IPG-1"]} onClose={vi.fn()} />
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
