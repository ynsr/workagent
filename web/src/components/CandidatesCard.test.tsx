import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { CandidatesCard } from "@/components/CandidatesCard"
import { ConfirmProvider } from "@/lib/confirm"

const { mutateAsync } = vi.hoisted(() => ({
  mutateAsync: vi.fn(() => Promise.resolve({ run_id: "run-1" })),
}))

vi.mock("@/lib/queries", () => ({
  queryKeys: {
    candidatesPrs: ["candidates", "prs"],
    candidatesIssues: () => ["candidates", "issues"],
    candidatesWorktrees: ["candidates", "worktrees"],
    harnessOptions: (name: string) => ["harnesses", "options", name],
  },
  useConfig: () => ({ data: { default_harness: "agy", supported_harnesses: ["agy"], harnesses: [] } }),
  useCreateRun: () => ({ mutateAsync }),
  useRepos: () => ({ data: [{ name: "proj", path: "/repos/proj" }] }),
  useCandidatesPrs: () => ({
    data: {
      prs: [
        {
          key: "github:o/r#12",
          number: 12,
          title: "Fix bug",
          url: "https://github.com/o/r/pull/12",
          branch: "fix/bug",
          updated: "2026-11-20T00:00:00Z",
          state: "open",
          repo: "o/r",
          repo_name: "proj",
          author: "alice",
        },
      ],
      warnings: [],
    },
    isPending: false,
    isError: false,
    isFetching: false,
  }),
  useCandidatesIssues: () => ({
    data: {
      issues: [
        {
          key: "IPG-77",
          title: "Add feature",
          url: "https://jira.example.com/browse/IPG-77",
          status: "To Do",
          created: "2026-11-20T00:00:00Z",
          assignee: "me",
          creator: "me",
          repo_hint: "proj",
        },
      ],
      warnings: [],
    },
    isPending: false,
    isError: false,
    isFetching: false,
  }),
  useCandidatesWorktrees: () => ({
    data: { worktrees: [], warnings: [] },
    isPending: false,
    isError: false,
    isFetching: false,
  }),
}))

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <ConfirmProvider>
          <CandidatesCard />
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

describe("CandidatesCard Register buttons", () => {
  it("registers a candidate PR/MR row with --register-only", async () => {
    const user = userEvent.setup()
    renderCard()

    await user.click(screen.getByRole("button", { name: "Register" }))
    await user.click(screen.getByRole("button", { name: "Register #12" }))

    expect(mutateAsync).toHaveBeenCalledWith({
      command: "start",
      args: ["https://github.com/o/r/pull/12", "--register-only", "--repo", "proj"],
      confirm: true,
    })
  })

  it("opens Register dialog for a candidate issue row and submits --register-only", async () => {
    const user = userEvent.setup()
    renderCard()

    await user.click(screen.getByRole("tab", { name: /Recent issues/ }))
    await user.click(screen.getByRole("button", { name: "Register" }))
    expect(screen.getByRole("heading", { name: "Register IPG-77" })).toBeInTheDocument()

    await user.click(screen.getByText("Select a repo…"))
    await user.click(screen.getAllByText("proj").at(-1)!)
    const registerBtns = screen.getAllByRole("button", { name: "Register" })
    await user.click(registerBtns[registerBtns.length - 1]!)
    await user.click(screen.getByRole("button", { name: "Register" }))

    expect(mutateAsync).toHaveBeenCalledWith({
      command: "start",
      args: ["IPG-77", "--register-only", "--repo", "proj", "--depth", "7"],
      confirm: true,
    })
  })
})
