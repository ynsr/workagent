import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ImmediateRunForm } from "@/components/ImmediateRunForm"
import { ConfirmProvider } from "@/lib/confirm"

const { mutateAsync, mockConfig } = vi.hoisted(() => ({
  mutateAsync: vi.fn(() => Promise.resolve({ run_id: "run-1" })),
  mockConfig: {
    default_harness: "agy",
    supported_harnesses: ["agy", "omp"],
    harnesses: [
      { name: "agy", label: "Antigravity (agy-guard)", binary: "agy-guard", available: true },
      { name: "omp", label: "OMP (omp)", binary: "omp", available: true },
    ],
  },
}))

vi.mock("@/lib/queries", () => ({
  queryKeys: { harnessOptions: (name: string) => ["harnesses", "options", name] },
  useActions: () => ({
    data: { actions: [{ label: "audit", name: "Audit", category: "worktree" }] },
  }),
  useConfig: () => ({ data: mockConfig }),
  useCreateRun: () => ({ mutateAsync }),
  useFlowTemplates: () => ({
    data: { templates: [{ label: "ship", name: "Ship", category: "worktree" }] },
  }),
  useRepos: () => ({ data: [] }),
}))

function renderRunForm(kind: "action" | "flow", preset: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <ConfirmProvider>
          <ImmediateRunForm kind={kind} preset={preset} worktreeRefs={["jira:IPG-1"]} />
        </ConfirmProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  localStorage.clear()
  mutateAsync.mockClear()
})

afterEach(cleanup)

describe("ImmediateRunForm harness", () => {
  it("renders HarnessPicker for action runs and emits --harness", async () => {
    const user = userEvent.setup()
    renderRunForm("action", "audit")

    await user.click(screen.getByRole("button", { name: /^Harness, model, and effort/ }))
    await user.click(screen.getByRole("radio", { name: "omp" }))
    await user.keyboard("{Escape}")
    await user.click(screen.getByRole("button", { name: "Run action now" }))
    await user.click(screen.getByRole("button", { name: "Run now" }))

    expect(mutateAsync).toHaveBeenCalledWith({
      command: "action",
      args: ["run", "audit", "jira:IPG-1", "--launch", "--harness", "omp", "--yes"],
      confirm: true,
    })
  })

  it("renders HarnessPicker for flow runs and emits --harness", async () => {
    const user = userEvent.setup()
    renderRunForm("flow", "ship")

    await user.click(screen.getByRole("button", { name: /^Harness, model, and effort/ }))
    await user.click(screen.getByRole("radio", { name: "agy" }))
    await user.keyboard("{Escape}")
    await user.click(screen.getByRole("button", { name: "Run flow now" }))
    await user.click(screen.getByRole("button", { name: "Run now" }))

    expect(mutateAsync).toHaveBeenCalledWith({
      command: "flow",
      args: ["run", "ship", "jira:IPG-1", "--harness", "agy", "--yes"],
      confirm: true,
    })
    expect(mutateAsync).toHaveBeenCalledWith({
      command: "flow",
      args: ["template", "update", "ship", "--harness", "agy", "--model", "", "--effort", "", "--yes"],
      confirm: false,
    })
  })
})
