import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
import { ConfirmProvider } from "@/lib/confirm"
import { PRODTEST_STATUS_HELP, Prodtests, filterProdtests } from "./Prodtests"
import type { ProdtestRow } from "@/lib/api"
import type * as QueriesModule from "@/lib/queries"

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
    useProdtests: settled({ prodtests: [] }),
    useLinks: settled({ trackers: {}, worktrees: {} }),
    useRepos: settled([]),
    useConfig: settled({ default_harness: "agy", supported_harnesses: ["agy"], harnesses: [] }),
    useCreateRun: () => ({ mutateAsync: vi.fn() }),
  }
})

afterEach(cleanup)

const ROWS: ProdtestRow[] = [
  { issue_key: "IPG-932", status: "passed", plan_path: "plans/p932.md", worktree_ref: "wt-932", last_run_id: "r1" },
  { issue_key: "IPG-933", status: "pending", plan_path: "plans/p933.md", worktree_ref: "", last_run_id: "" },
]

describe("filterProdtests", () => {
  it("matches issue/plan/worktree/run substring", () => {
    expect(filterProdtests(ROWS, { q: "932", status: "" })).toHaveLength(1)
    expect(filterProdtests(ROWS, { q: "plans/p933", status: "" })[0]?.issue_key).toBe("IPG-933")
    expect(filterProdtests(ROWS, { q: "wt-932", status: "" })).toHaveLength(1)
    expect(filterProdtests(ROWS, { q: "nothing", status: "" })).toHaveLength(0)
  })
  it("filters by exact status", () => {
    expect(filterProdtests(ROWS, { q: "", status: "passed" })).toHaveLength(1)
    expect(filterProdtests(ROWS, { q: "IPG", status: "pending" })[0]?.issue_key).toBe("IPG-933")
    expect(filterProdtests(ROWS, { q: "", status: "failed" })).toHaveLength(0)
  })
})

describe("PRODTEST_STATUS_HELP", () => {
  it("covers exactly the backend status set (store_sqlite.PRODTEST_STATUSES)", () => {
    expect(Object.keys(PRODTEST_STATUS_HELP).sort()).toEqual(
      ["blocked", "failed", "passed", "pending", "pending_prodtest", "running"],
    )
  })
})

describe("Prodtests launch drawer", () => {
  it("opens the Plan / Run drawer from the header button", async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <ConfirmProvider>
            <Prodtests />
          </ConfirmProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    )

    await user.click(screen.getByRole("button", { name: "Plan / Run" }))
    expect(screen.getByRole("dialog", { name: "Plan / Run Prodtest" })).toBeInTheDocument()
  })
})
