import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { WorktreeFeedbackTimeline } from "./WorktreeFeedbackTimeline"
import { api, type WorktreeEntry, type WorktreeTimeline } from "@/lib/api"

const mockTimeline = vi.fn()
const mockMutate = vi.fn()

vi.mock("@/lib/queries", async () => {
  const actual = await vi.importActual<typeof import("@/lib/queries")>("@/lib/queries")
  return {
    ...actual,
    useWorktreeTimeline: (...args: unknown[]) => mockTimeline(...args),
    useCreateRun: () => ({
      mutate: mockMutate,
      isPending: false,
    }),
  }
})

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api")
  return {
    ...actual,
    api: {
      ...actual.api,
      worktreeTimeline: vi.fn(),
    },
  }
})

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

describe("WorktreeFeedbackTimeline", () => {
  beforeEach(() => {
    mockTimeline.mockReset()
    mockMutate.mockReset()
    vi.mocked(api.worktreeTimeline).mockReset()
  })

  afterEach(() => {
    cleanup()
  })

  it("renders failing checks, unresolved threads, events, and inline actions", () => {
    const timeline: WorktreeTimeline = {
      ref: "jira:IPG-146",
      checks: [
        {
          id: 1,
          worktree_ref: "jira:IPG-146",
          name: "pytest",
          commit_sha: "abc",
          status: "completed",
          conclusion: "failure",
          url: "https://ci/1",
          log_tail: "AssertionError: expected 1 == 2",
          updated_at: "2026-11-22T01:00:00Z",
        },
        {
          id: 2,
          worktree_ref: "jira:IPG-146",
          name: "lint",
          commit_sha: "abc",
          status: "completed",
          conclusion: "success",
          url: "https://ci/2",
          log_tail: "",
          updated_at: "2026-11-22T01:00:00Z",
        },
      ],
      threads: [
        {
          id: 10,
          worktree_ref: "jira:IPG-146",
          thread_id: "t1",
          author: "alice",
          file: "src/app.py",
          line: 42,
          body: "Please handle None here",
          resolved: false,
          is_bot: false,
          updated_at: "2026-11-22T01:01:00Z",
        },
      ],
      events: [
        {
          id: 100,
          worktree_ref: "jira:IPG-146",
          event_type: "ci_failed",
          summary: "CI failed: pytest",
          details: {},
          created_at: "2026-11-22T01:00:00Z",
        },
      ],
      summary: { failing_checks: ["pytest"], unresolved_threads: 1 },
    }
    mockTimeline.mockReturnValue({ data: timeline, isLoading: false, isError: false })

    const onFixCi = vi.fn()
    const onFixComments = vi.fn()

    wrap(
      <WorktreeFeedbackTimeline
        worktreeKey="jira:IPG-146"
        onFixCi={onFixCi}
        onFixComments={onFixComments}
      />,
    )

    expect(screen.getByText("pytest")).toBeInTheDocument()
    expect(screen.queryByText("lint")).not.toBeInTheDocument()
    expect(screen.getByText("Please handle None here")).toBeInTheDocument()
    expect(screen.getByText("CI failed: pytest")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /Fix CI/i }))
    expect(onFixCi).toHaveBeenCalledWith(["pytest"])

    fireEvent.click(screen.getByRole("button", { name: /Fix comments/i }))
    expect(onFixComments).toHaveBeenCalledTimes(1)
  })

  it("renders Cleanup action when PR is merged and ready_to_merge", () => {
    mockTimeline.mockReturnValue({
      data: {
        ref: "jira:IPG-146",
        checks: [],
        threads: [],
        events: [],
        summary: { failing_checks: [], unresolved_threads: 0 },
      },
      isLoading: false,
      isError: false,
    })

    const onCleanup = vi.fn()
    const entry: WorktreeEntry = {
      branch: "feat/146",
      worktree: "/tmp/wt",
      repo: "/tmp/repo",
      kanban_column: "ready_to_merge",
      display_status: "Merged · Ready for cleanup",
      pr_detail: {
        number: 146,
        state: "merged",
        url: "https://github.com/o/r/pull/146",
        title: "T09 timeline",
        author: "alice",
        tool: "gh",
      },
    }

    wrap(
      <WorktreeFeedbackTimeline
        worktreeKey="jira:IPG-146"
        entry={entry}
        onCleanup={onCleanup}
      />,
    )

    expect(screen.getByText("Merged · Ready for cleanup")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /Cleanup/i }))
    expect(onCleanup).toHaveBeenCalledTimes(1)
  })

  it("renders Cleanup action when PR is merged even if kanban_column is not ready_to_merge", () => {
    mockTimeline.mockReturnValue({
      data: {
        ref: "jira:IPG-146",
        checks: [],
        threads: [],
        events: [],
        summary: { failing_checks: [], unresolved_threads: 0 },
      },
      isLoading: false,
      isError: false,
    })

    const onCleanup = vi.fn()
    const entry: WorktreeEntry = {
      branch: "feat/146",
      worktree: "/tmp/wt",
      repo: "/tmp/repo",
      kanban_column: "in_review",
      pr_detail: {
        number: 146,
        state: "merged",
        url: "https://github.com/o/r/pull/146",
        title: "T09 timeline",
        author: "alice",
        tool: "gh",
      },
    }

    wrap(
      <WorktreeFeedbackTimeline
        worktreeKey="jira:IPG-146"
        entry={entry}
        onCleanup={onCleanup}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: /Cleanup/i }))
    expect(onCleanup).toHaveBeenCalledTimes(1)
  })

  it("renders empty state when no checks, threads, or events exist", () => {
    mockTimeline.mockReturnValue({
      data: {
        ref: "jira:IPG-146",
        checks: [],
        threads: [],
        events: [],
        summary: { failing_checks: [], unresolved_threads: 0 },
      },
      isLoading: false,
      isError: false,
    })

    wrap(<WorktreeFeedbackTimeline worktreeKey="jira:IPG-146" />)
    expect(
      screen.getByText(/No CI checks, review threads, or timeline events recorded yet/i),
    ).toBeInTheDocument()
  })

  it("Fix CI launches adhoc run with failing job URL and cached 50-line log tail", async () => {
    const logLines = Array.from({ length: 60 }, (_, i) => `log-${i + 1}`).join("\n")
    mockTimeline.mockReturnValue({
      data: {
        ref: "jira:IPG-146",
        checks: [
          {
            id: 1,
            worktree_ref: "jira:IPG-146",
            name: "test",
            commit_sha: "abc",
            status: "failed",
            conclusion: "failure",
            url: "https://git.jibit.cloud/server/projectx/-/jobs/203729",
            log_tail: logLines,
            updated_at: "2026-11-24T01:00:00Z",
          },
        ],
        threads: [],
        events: [],
        summary: { failing_checks: ["test"], unresolved_threads: 0 },
      },
      isLoading: false,
      isError: false,
    })

    wrap(<WorktreeFeedbackTimeline worktreeKey="jira:IPG-146" />)
    fireEvent.click(screen.getByRole("button", { name: /Fix CI/i }))

    await waitFor(() => {
      expect(mockMutate).toHaveBeenCalledTimes(1)
    })
    expect(api.worktreeTimeline).not.toHaveBeenCalled()
    const payload = mockMutate.mock.calls[0]?.[0] as { command: string; args: string[]; confirm: boolean }
    expect(payload.command).toBe("adhoc")
    expect(payload.confirm).toBe(true)
    expect(payload.args[0]).toBe("jira:IPG-146")
    expect(payload.args[1]).toBe("--prompt")
    const prompt = payload.args[2] ?? ""
    expect(prompt).toContain("https://git.jibit.cloud/server/projectx/-/jobs/203729")
    expect(prompt).not.toContain("log-10\n")
    expect(prompt).toContain("log-11")
    expect(prompt).toContain("log-60")
  })

  it("Fix CI fetches missing logs via fetchLogs=true before launching run", async () => {
    mockTimeline.mockReturnValue({
      data: {
        ref: "github:o/r#1",
        checks: [
          {
            id: 1,
            worktree_ref: "github:o/r#1",
            name: "pytest",
            commit_sha: "abc",
            status: "completed",
            conclusion: "failure",
            url: "https://github.com/o/r/actions/runs/50/job/60",
            log_tail: "",
            updated_at: "2026-11-24T01:00:00Z",
          },
        ],
        threads: [],
        events: [],
        summary: { failing_checks: ["pytest"], unresolved_threads: 0 },
      },
      isLoading: false,
      isError: false,
    })
    vi.mocked(api.worktreeTimeline).mockResolvedValue({
      ref: "github:o/r#1",
      checks: [
        {
          id: 1,
          worktree_ref: "github:o/r#1",
          name: "pytest",
          commit_sha: "abc",
          status: "completed",
          conclusion: "failure",
          url: "https://github.com/o/r/actions/runs/50/job/60",
          log_tail: "E AssertionError: 1 != 2",
          updated_at: "2026-11-24T01:00:00Z",
        },
      ],
      threads: [],
      events: [],
      summary: { failing_checks: ["pytest"], unresolved_threads: 0 },
    })

    wrap(<WorktreeFeedbackTimeline worktreeKey="github:o/r#1" />)
    fireEvent.click(screen.getByRole("button", { name: /Fix CI/i }))

    await waitFor(() => {
      expect(mockMutate).toHaveBeenCalledTimes(1)
    })
    expect(api.worktreeTimeline).toHaveBeenCalledWith("github:o/r#1", 100, { fetchLogs: true })
    const payload = mockMutate.mock.calls[0]?.[0] as { args: string[] }
    const prompt = payload.args[2] ?? ""
    expect(prompt).toContain("https://github.com/o/r/actions/runs/50/job/60")
    expect(prompt).toContain("E AssertionError: 1 != 2")
  })
})
