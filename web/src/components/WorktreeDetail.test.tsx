import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { WorktreeDetail } from "@/components/WorktreeDetail"
import type { WorktreeEntry } from "@/lib/api"

const { mockConfig } = vi.hoisted(() => ({
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
  queryKeys: { runs: ["runs"], harnessOptions: (name: string) => ["harnesses", "options", name] },
  useActions: () => ({ data: { actions: [] } }),
  useWorktreeWatcher: () => ({ data: null }),
  useConfig: () => ({ data: mockConfig }),
  useCreateRun: () => ({ mutateAsync: vi.fn(), mutate: vi.fn(), isPending: false }),
  useWorktreeTimeline: () => ({
    data: { ref: "jira:IPG-1", checks: [], threads: [], events: [], summary: { failing_checks: [], unresolved_threads: 0 } },
    isLoading: false,
    isError: false,
  }),
}))

const ENTRY: WorktreeEntry = {
  branch: "feat/test",
  worktree: "/tmp/wt",
  repo: "proj",
}

function renderDetail() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <WorktreeDetail
          entry={ENTRY}
          worktreeKey="jira:IPG-1"
          mode="view"
          onSave={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(cleanup)

describe("WorktreeDetail terminal harness", () => {
  it("renders compact HarnessPicker and forwards selected harness to /terminal", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: 1, session_file: "", worktree: "/tmp/wt", mode: "harness" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    const user = userEvent.setup()
    renderDetail()

    await user.click(screen.getByRole("button", { name: /^Harness, model, and effort/ }))
    await user.click(screen.getByRole("radio", { name: "omp" }))
    await user.keyboard("{Escape}")
    await user.click(screen.getByRole("button", { name: "Harness" }))

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/worktrees/jira%3AIPG-1/terminal",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ mode: "harness", harness: "omp" }),
      }),
    )
  })

  it("omits harness from /terminal body when default is selected", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: 1, session_file: "", worktree: "/tmp/wt", mode: "harness" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    const user = userEvent.setup()
    renderDetail()

    await user.click(screen.getByRole("button", { name: "Harness" }))

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/worktrees/jira%3AIPG-1/terminal",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ mode: "harness" }),
      }),
    )
  })
})
