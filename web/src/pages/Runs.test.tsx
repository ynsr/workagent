import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it, vi } from "vitest"
import { Runs } from "@/pages/Runs"
import type { Run } from "@/lib/api"

const { mutateAsync } = vi.hoisted(() => ({ mutateAsync: vi.fn(() => Promise.resolve(undefined)) }))

const RUNS: Run[] = [
  {
    // The persisted fix-comments shape: flags dropped, distinguished only by
    // the stored session_type.
    id: "db-1", command: "review", args: ["jira:IPG-1"],
    state: "succeeded", exit_code: 0, truncated: false, created: 1_760_000_000,
    target: "review:jira:IPG-1", session_file: "/home/x/runs/omp/IPG-1/fix_comments/a.jsonl",
    transcript_ready: true,
    session_type: "fix_comments", worktree: "/home/x/wt", last_seq: 2,
  },
  {
    id: "db-2", command: "sync", args: ["jira:IPG-2"],
    state: "succeeded", exit_code: 0, truncated: false, created: 1_759_000_000,
    target: "sync:jira:IPG-2", session_file: "", transcript_ready: false, session_type: null,
    worktree: "", last_seq: 1,
  },
  {
    // Pre-migration review row: no stored type, so the command labels it.
    id: "db-3", command: "review", args: ["jira:IPG-3"],
    state: "succeeded", exit_code: 0, truncated: false, created: 1_758_000_000,
    target: "review:jira:IPG-3", session_file: "", transcript_ready: false,
    session_type: null, worktree: "", last_seq: 1,
  },
  {
    // Preview row: the stored path is the --session-dir scope hint, not a
    // transcript omp minted, so resume works but copy must not offer it.
    id: "db-4", command: "start", args: ["jira:IPG-4"],
    state: "succeeded", exit_code: 0, truncated: false, created: 1_757_000_000,
    target: "start:jira:IPG-4", session_file: "/home/x/runs/omp/IPG-4",
    transcript_ready: false, session_type: "start",
    worktree: "/home/x/wt", last_seq: 1,
  },
  {
    id: "db-5", command: "start", args: ["jira:IPG-5"],
    state: "succeeded", exit_code: 0, truncated: false, created: 1_756_000_000,
    target: "start:jira:IPG-5", session_file: "/home/x/runs/agy/IPG-5/conv-1.jsonl",
    transcript_ready: true, session_type: "start",
    worktree: "/home/x/wt", last_seq: 1,
    harness_name: "agy",
    resume_command: "cd '/home/x/wt' && agy-guard --conversation conv-1",
  },
]

vi.mock("@/lib/queries", () => ({
  useRuns: () => ({ data: RUNS, isPending: false, isFetching: false, isError: false, error: null, refetch: vi.fn() }),
  useRepos: () => ({ data: [] }),
  useFlows: () => ({ data: { flows: [] } }),
  useCancelRun: () => ({ isPending: false, mutateAsync }),
  useResumeRun: () => ({ isPending: false, mutateAsync }),
}))

function renderRuns() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <Runs />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe("Runs transcript affordances", () => {
  it("labels from session_type and falls back to the command", () => {
    renderRuns()
    // Table (≥sm) and card (<sm) both render the label.
    expect(screen.getAllByText("Fix comments")).not.toHaveLength(0)
    expect(screen.getAllByText("Sync")).not.toHaveLength(0)
    expect(screen.getAllByText("Review")).not.toHaveLength(0)
  })

  it("offers resume/copy only for runs that own a transcript", () => {
    renderRuns()
    expect(screen.getAllByLabelText(/Resume transcript of run db-1 in terminal/)).not.toHaveLength(0)
    expect(screen.queryByLabelText(/Resume transcript of run db-2/)).toBeNull()
  })

  it("disables copy when the stored path is only a --session-dir hint", () => {
    renderRuns()
    const copies = screen.getAllByLabelText(/Copy resume command for run db-4/)
    expect(copies.length).toBeGreaterThan(0)
    for (const b of copies) expect((b as HTMLButtonElement).disabled).toBe(true)
    // Resume stays live: the endpoint resolves the minted transcript itself.
    for (const b of screen.getAllByLabelText(/Resume transcript of run db-4 in terminal/))
      expect((b as HTMLButtonElement).disabled).toBe(false)
  })

  it("enables copy for a ready transcript", () => {
    renderRuns()
    const copies = screen.getAllByLabelText(/Copy resume command for run db-1/)
    expect(copies.length).toBeGreaterThan(0)
    for (const b of copies) expect((b as HTMLButtonElement).disabled).toBe(false)
  })

  it("renders server resume_command for agy runs", () => {
    renderRuns()
    const resumeBtns = screen.getAllByLabelText(/Resume transcript of run db-5 in terminal/)
    expect(resumeBtns.length).toBeGreaterThan(0)
    for (const b of resumeBtns) {
      expect(b).toHaveAttribute("title", "cd '/home/x/wt' && agy-guard --conversation conv-1")
    }
    const copyBtns = screen.getAllByLabelText(/Copy resume command for run db-5/)
    expect(copyBtns.length).toBeGreaterThan(0)
    for (const b of copyBtns) {
      expect(b).toHaveAttribute("title", "cd '/home/x/wt' && agy-guard --conversation conv-1")
    }
  })
})
