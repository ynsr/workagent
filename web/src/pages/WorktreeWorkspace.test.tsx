import { describe, expect, it, vi, beforeEach, afterEach } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom"
import { WorktreeWorkspace } from "@/pages/WorktreeWorkspace"
import { api } from "@/lib/api"

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api")
  return {
    ...actual,
    api: {
      ...actual.api,
      statusAll: vi.fn(),
      runs: vi.fn(),
      flows: vi.fn(),
      run: vi.fn(),
      config: vi.fn(),
      harnessOptions: vi.fn(),
      followUpRun: vi.fn(),
      createRun: vi.fn(),
      worktreeTimeline: vi.fn(),
    },
  }
})

function renderWorkspace(ref = "jira:IPG-42") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/worktrees/${encodeURIComponent(ref)}`]}>
        <Routes>
          <Route path="/worktrees/:ref" element={<WorktreeWorkspace />} />
          <Route path="/" element={<div>Dashboard Home</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("WorktreeWorkspace", () => {
  afterEach(() => {
    cleanup()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.statusAll).mockResolvedValue({
      "jira:IPG-42": {
        worktree: "/tmp/wt/42",
        branch: "feat/42",
        pr: "PR #42",
        ci: "success",
        display_status: "Ready to merge",
        kanban_column: "ready_to_merge",
        prodtest: "passed",
      },
    } as never)
    vi.mocked(api.runs).mockResolvedValue([
      {
        id: "r1",
        command: "start",
        args: ["jira:IPG-42"],
        target: "start:jira:IPG-42",
        worktree: "/tmp/wt/42",
        state: "succeeded",
        created: 1700000000,
      },
    ] as never)
    vi.mocked(api.flows).mockResolvedValue({ flows: [] } as never)
    vi.mocked(api.run).mockResolvedValue({
      id: "r1",
      command: "start",
      args: ["jira:IPG-42"],
      target: "start:jira:IPG-42",
      state: "succeeded",
      lines: [{ seq: 0, text: 'ACP:{"t":"message","id":"s1","text":"Done implementing."}' }],
      pending_question: null,
    } as never)
    vi.mocked(api.config).mockResolvedValue({ harnesses: [{ name: "agy", label: "AGY" }], default_harness: "agy" } as never)
    vi.mocked(api.harnessOptions).mockResolvedValue({
      harness: "agy",
      supported: true,
      models: [{ id: "m1", name: "Model 1" }],
      efforts: [{ id: "high", name: "High" }],
      current_model: "m1",
      current_effort: "high",
    } as never)
    vi.mocked(api.followUpRun).mockResolvedValue({
      run_id: "r2",
      continued_from_run_id: 1,
      worktree_ref: "jira:IPG-42",
    })
  })

  it("renders breadcrumb, transcript, inspector tabs, and submits follow-up prompt", async () => {
    renderWorkspace("jira:IPG-42")

    expect(await screen.findByText("Dashboard")).toBeTruthy()
    expect(await screen.findByText("Done implementing.")).toBeTruthy()
    expect(screen.getByTestId("inspector-summary")).toBeTruthy()

    // Switch inspector tab to Diff / Files
    fireEvent.click(screen.getByRole("tab", { name: /Diff \/ Files/i }))
    expect(screen.getByTestId("inspector-diff")).toBeTruthy()

    // Use quick action chip and submit follow-up
    fireEvent.click(screen.getByRole("button", { name: "Run tests" }))
    fireEvent.click(screen.getByRole("button", { name: /Send follow-up/i }))

    await waitFor(() => {
      expect(api.followUpRun).toHaveBeenCalledWith(
        "r1",
        expect.objectContaining({
          prompt: "Run the test suite and report any failures.",
          run_mode: "acp",
        }),
      )
    })
  })

  it("populates Fix CI quick chip with failing CI job URL and logs when CI fails", async () => {
    vi.mocked(api.statusAll).mockResolvedValue({
      "jira:IPG-42": {
        worktree: "/tmp/wt/42",
        branch: "feat/42",
        pr: "MR #42",
        ci: "failure",
        ci_url: "https://git.jibit.cloud/server/projectx/-/jobs/203729",
        display_status: "CI failing",
      },
    } as never)
    vi.mocked(api.worktreeTimeline).mockResolvedValue({
      ref: "jira:IPG-42",
      checks: [
        {
          id: 1,
          worktree_ref: "jira:IPG-42",
          name: "test",
          commit_sha: "abc",
          status: "failed",
          conclusion: "failure",
          url: "https://git.jibit.cloud/server/projectx/-/jobs/203729",
          log_tail: "FAILED tests/test_app.py::test_x",
          updated_at: "2026-11-24T00:00:00Z",
        },
      ],
      threads: [],
      events: [],
      summary: { failing_checks: ["test"], unresolved_threads: 0 },
    })

    renderWorkspace("jira:IPG-42")
    await screen.findByText("Done implementing.")
    fireEvent.click(screen.getByRole("button", { name: "Fix CI" }))

    const textarea = screen.getByLabelText("Follow-up prompt") as HTMLTextAreaElement
    await waitFor(() => {
      expect(textarea.value).toContain("https://git.jibit.cloud/server/projectx/-/jobs/203729")
      expect(textarea.value).toContain("FAILED tests/test_app.py::test_x")
    })
  })

  it("ignores stale Fix CI log resolution when refKey changes before completion", async () => {
    let resolveTimeline!: (val: unknown) => void
    const pendingTimeline = new Promise((res) => {
      resolveTimeline = res
    })
    vi.mocked(api.statusAll).mockResolvedValue({
      "jira:IPG-42": {
        worktree: "/tmp/wt/42",
        branch: "feat/42",
        pr: "MR #42",
        ci: "failure",
        ci_url: "https://git.jibit.cloud/server/projectx/-/jobs/42",
        display_status: "CI failing",
      },
      "jira:IPG-99": {
        worktree: "/tmp/wt/99",
        branch: "feat/99",
        pr: "MR #99",
        ci: "success",
        display_status: "Ready to merge",
      },
    } as never)
    vi.mocked(api.worktreeTimeline).mockImplementation(((ref: string, _limit?: number, opts?: { fetchLogs?: boolean }) => {
      if (ref === "jira:IPG-42" && opts?.fetchLogs) {
        return pendingTimeline as never
      }
      return Promise.resolve({
        ref,
        checks: [],
        threads: [],
        events: [],
        summary: { failing_checks: [], unresolved_threads: 0 },
      }) as never
    }) as never)

    function NavHelper() {
      const navigate = useNavigate()
      return (
        <button type="button" onClick={() => navigate("/worktrees/jira%3AIPG-99")}>
          Switch Worktree
        </button>
      )
    }

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/worktrees/jira%3AIPG-42"]}>
          <NavHelper />
          <Routes>
            <Route path="/worktrees/:ref" element={<WorktreeWorkspace />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await screen.findByText("Done implementing.")
    fireEvent.click(screen.getByRole("button", { name: "Fix CI" }))
    expect(screen.getByRole("button", { name: "Fetching logs…" })).toBeDisabled()

    fireEvent.click(screen.getByRole("button", { name: "Switch Worktree" }))
    await screen.findByText("Starts a new adhoc run on jira:IPG-99")
    expect(screen.getByRole("button", { name: "Fix CI" })).not.toBeDisabled()

    resolveTimeline({
      ref: "jira:IPG-42",
      checks: [
        {
          id: 1,
          worktree_ref: "jira:IPG-42",
          name: "test",
          commit_sha: "abc",
          status: "failed",
          conclusion: "failure",
          url: "https://git.jibit.cloud/server/projectx/-/jobs/42",
          log_tail: "STALE LOG",
          updated_at: "2026-11-24T00:00:00Z",
        },
      ],
      threads: [],
      events: [],
      summary: { failing_checks: ["test"], unresolved_threads: 0 },
    })

    await waitFor(() => {
      const textarea = screen.getByLabelText("Follow-up prompt") as HTMLTextAreaElement
      expect(textarea.value).toBe("")
    })
  })
})
