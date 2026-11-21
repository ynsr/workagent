import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ConfirmProvider } from "@/lib/confirm"
import { setVerbose } from "@/lib/settings"
import { PRODTEST_STATUS_HELP, Prodtests, filterProdtests, planBasename } from "./Prodtests"
import type { ProdtestRow } from "@/lib/api"
import type * as QueriesModule from "@/lib/queries"

const { MOCK_ROWS } = vi.hoisted(() => ({
  MOCK_ROWS: [
    {
      issue_key: "IPG-1069",
      status: "pending",
      plan_path: "/home/bs/dev/repo/.workagent/prodtests/IPG-1069.yaml",
      plan_revision: 3,
      plan_risk: "medium",
      worktree_ref: "jira:IPG-1069",
      last_run_id: "",
    },
  ] as ProdtestRow[],
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
    useProdtests: settled({ prodtests: MOCK_ROWS }),
    useLinks: settled({ trackers: {}, worktrees: {} }),
    useRepos: settled([]),
    useConfig: settled({ default_harness: "agy", supported_harnesses: ["agy"], harnesses: [] }),
    useCreateRun: () => ({ mutateAsync: vi.fn() }),
  }
})

const ROWS: ProdtestRow[] = [
  { issue_key: "IPG-932", status: "passed", plan_path: "plans/p932.md", worktree_ref: "wt-932", last_run_id: "r1" },
  { issue_key: "IPG-933", status: "pending", plan_path: "plans/p933.md", worktree_ref: "", last_run_id: "" },
]

function renderProdtests() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <ConfirmProvider>
          <Prodtests />
        </ConfirmProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(cleanup)

describe("planBasename", () => {
  it("extracts filename from POSIX and Windows paths", () => {
    expect(planBasename("/a/b/IPG-1069.yaml")).toBe("IPG-1069.yaml")
    expect(planBasename("C:\\work\\IPG-1069.yaml")).toBe("IPG-1069.yaml")
    expect(planBasename("IPG-1069.yaml")).toBe("IPG-1069.yaml")
  })
})

describe("Prodtests UI density", () => {
  it("hides GET /api/prodtests subtitle by default and shows it when verbose is enabled", () => {
    renderProdtests()
    expect(screen.queryByText(/GET \/api\/prodtests/)).not.toBeInTheDocument()
    cleanup()
    setVerbose(true)
    renderProdtests()
    expect(screen.getByText(/GET \/api\/prodtests/)).toBeInTheDocument()
  })

  it("renders plan_path as a compact basename pill with tooltip and copy button", () => {
    renderProdtests()
    const pill = screen.getByText("IPG-1069.yaml · rev3 · medium")
    expect(pill).toHaveAttribute("title", "/home/bs/dev/repo/.workagent/prodtests/IPG-1069.yaml")
    expect(screen.getByRole("button", { name: "Copy plan path for IPG-1069" })).toBeInTheDocument()
  })
})

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
