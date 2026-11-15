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
    session_type: "fix_comments", worktree: "/home/x/wt", last_seq: 2,
  },
  {
    id: "db-2", command: "sync", args: ["jira:IPG-2"],
    state: "succeeded", exit_code: 0, truncated: false, created: 1_759_000_000,
    target: "sync:jira:IPG-2", session_file: "", session_type: null,
    worktree: "", last_seq: 1,
  },
  {
    // Pre-migration review row: no stored type, so the command labels it.
    id: "db-3", command: "review", args: ["jira:IPG-3"],
    state: "succeeded", exit_code: 0, truncated: false, created: 1_758_000_000,
    target: "review:jira:IPG-3", session_file: "", session_type: null,
    worktree: "", last_seq: 1,
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
})
