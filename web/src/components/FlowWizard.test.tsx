import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it, vi } from "vitest"
import { FlowWizard } from "@/components/FlowWizard"
import * as flowExecution from "@/lib/flowExecution"
import type { FlowTemplateRow } from "@/lib/api"

const { mutateAsync } = vi.hoisted(() => ({ mutateAsync: vi.fn(async () => ({ run_id: "run-1" })) }))
vi.mock("@/lib/queries", () => ({
  useActions: () => ({
    data: { actions: [{ label: "lint", name: "Lint" }, { label: "typecheck", name: "Typecheck" }] },
  }),
  useCreateRun: () => ({ mutateAsync }),
  useRepos: () => ({ data: [] }),
}))

const TEMPLATE = {
  name: "ship",
  label: "ship",
  steps: [
    { kind: "start", config: { repo: "proj" } },
    { kind: "action", config: {}, action_label: "lint" },
  ],
} as unknown as FlowTemplateRow

function renderWizard(onClose: () => void = vi.fn()) {
  const client = new QueryClient()
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <FlowWizard template={TEMPLATE} refs={["jira:IPG-1"]} onClose={onClose} />
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

describe("FlowWizard run", () => {
  it("closes the modal before the sequential queue settles", async () => {
    // Block the queue in `awaitRunSettled` so the test can observe close
    // order: with the old await-then-close code this times out instead.
    const spy = vi.spyOn(flowExecution, "awaitRunSettled").mockImplementation(() => {
      let resolve!: () => void
      const promise = new Promise<void>((r) => { resolve = r })
      const t = globalThis.setTimeout(resolve, 5000)
      return promise.finally(() => globalThis.clearTimeout(t))
    })
    try {
      const onClose = vi.fn()
      renderWizard(onClose)
      fireEvent.click(screen.getByRole("button", { name: "Run flow" }))
      await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    } finally {
      spy.mockRestore()
    }
  })
})
