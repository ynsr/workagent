import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ConfirmProvider } from "@/lib/confirm"
import { Dashboard } from "@/pages/Dashboard"
import type * as QueriesModule from "@/lib/queries"

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

vi.mock("@/lib/queries", async () => {
  const actual = await vi.importActual<typeof QueriesModule>("@/lib/queries")
  const settled = <T,>(data: T) => () => ({
    data,
    isPending: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  })
  return {
    ...actual,
    useConfig: () => ({ data: mockConfig }),
    useCreateRun: () => ({ mutateAsync }),
    useStatusAll: settled({}),
    useStatusInactive: settled({}),
    useStatusDeleted: settled({}),
    useInfo: settled({ version: "1", host: "127.0.0.1", port: 8000, network_exposed: false }),
    useRepos: settled([]),
    useLinks: settled({ trackers: {}, worktrees: {} }),
    useFlows: settled({ flows: [] }),
    useActions: settled({ actions: [] }),
    useFlowTemplates: settled({ templates: [] }),
    useRuns: settled([]),
    useCandidatesPrs: settled({ prs: [], warnings: [] }),
    useCandidatesIssues: settled({ issues: [], warnings: [] }),
    useCandidatesWorktrees: settled({ worktrees: [], warnings: [] }),
    useStopTerminal: () => ({ mutate: vi.fn() }),
  }
})

function renderDashboard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <ConfirmProvider>
          <Dashboard />
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

describe("Dashboard bulk confirm harness", () => {
  it("forwards --harness from Sync all confirm dialog", async () => {
    const user = userEvent.setup()
    renderDashboard()

    await user.click(screen.getByRole("button", { name: "Bulk actions" }))
    await user.click(screen.getByRole("button", { name: "Sync all" }))
    await user.click(screen.getByRole("radio", { name: "omp" }))
    await user.click(screen.getByRole("button", { name: "Sync all" }))

    expect(mutateAsync).toHaveBeenCalledWith({
      command: "sync",
      args: ["--all", "--harness", "omp"],
      confirm: true,
    })
  })

  it("forwards --harness from Review all confirm dialog", async () => {
    const user = userEvent.setup()
    renderDashboard()

    await user.click(screen.getByRole("button", { name: "Bulk actions" }))
    await user.click(screen.getByRole("button", { name: "Review all" }))
    await user.click(screen.getByRole("radio", { name: "agy" }))
    await user.click(screen.getByRole("button", { name: "Review all" }))

    expect(mutateAsync).toHaveBeenCalledWith({
      command: "review",
      args: ["--all", "--harness", "agy"],
      confirm: true,
    })
  })

  it("forwards --harness from Fix all PR comments confirm dialog", async () => {
    const user = userEvent.setup()
    renderDashboard()

    await user.click(screen.getByRole("button", { name: "Bulk actions" }))
    await user.click(screen.getByRole("button", { name: "Fix all PR comments" }))
    await user.click(screen.getByRole("radio", { name: "omp" }))
    await user.click(screen.getByRole("button", { name: "Fix all" }))

    expect(mutateAsync).toHaveBeenCalledWith({
      command: "review",
      args: ["--all", "--fix-comments", "--harness", "omp"],
      confirm: true,
    })
  })

  it("opens and closes the New Task launch drawer", async () => {
    const user = userEvent.setup()
    renderDashboard()

    const trigger = screen.getByRole("button", { name: "New Task" })
    await user.click(trigger)
    expect(screen.getByRole("dialog", { name: "New Task" })).toBeInTheDocument()

    await user.keyboard("{Escape}")
    expect(screen.queryByRole("dialog", { name: "New Task" })).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })
})
