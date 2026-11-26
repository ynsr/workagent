import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { FixDefaultDialog, ReleaseCard } from "@/components/ReleaseCard"
import { ConfirmProvider } from "@/lib/confirm"
import type { ReleaseStatus, RunLine } from "@/lib/api"

const { mutateAsync, mockRun, mockToastSuccess, mockConfig, mockRepos, mockStatuses } = vi.hoisted(() => ({
  mutateAsync: vi.fn(() => Promise.resolve({ run_id: "run-1" })),
  mockRun: vi.fn(() => Promise.resolve({ state: "succeeded", lines: [] as RunLine[] })),
  mockToastSuccess: vi.fn(),
  mockConfig: {
    default_harness: "agy",
    supported_harnesses: ["agy", "omp"],
    harnesses: [
      { name: "agy", label: "Antigravity (agy-guard)", binary: "agy-guard", available: true },
      { name: "omp", label: "OMP (omp)", binary: "omp", available: true },
    ],
  },
  mockRepos: { value: [] as { name: string; path: string; release_branch?: string }[] },
  mockStatuses: { value: {} as Record<string, ReleaseStatus> },
}))

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api")
  return { ...actual, api: { ...actual.api, run: mockRun } }
})

vi.mock("sonner", () => ({
  toast: { success: mockToastSuccess, error: vi.fn(), info: vi.fn() },
}))

vi.mock("@/lib/queries", () => ({
  queryKeys: {
    harnessOptions: (name: string) => ["harnesses", "options", name],
    release: (name: string) => ["release", name],
    repos: ["repos"],
  },
  useConfig: () => ({ data: mockConfig }),
  useCreateRun: () => ({ mutateAsync }),
  useRepos: () => ({ data: mockRepos.value, isPending: false, isError: false, isFetching: false }),
  useReleaseStatus: (name: string) => ({ data: mockStatuses.value[name], isPending: false, isError: false, isFetching: false }),
  useReleaseStatuses: (names: string[]) => names.map((n) => ({ data: mockStatuses.value[n] })),
}))

const STATUS: ReleaseStatus = {
  repo: "proj",
  path: "/repos/proj",
  default_branch: "main",
  release_branch: "release",
  diff_count: 2,
  behind: 1,
  releasable: true,
  reason: "ok",
}

function renderDialog() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <ConfirmProvider>
          <FixDefaultDialog status={STATUS} onClose={vi.fn()} />
        </ConfirmProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  localStorage.clear()
  mutateAsync.mockClear()
  mockToastSuccess.mockClear()
})

afterEach(cleanup)

describe("FixDefaultDialog harness", () => {
  it("renders HarnessPicker and emits --harness in release fix-default args", async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.click(screen.getByRole("button", { name: /^Harness, model, and effort/ }))
    await user.click(screen.getByRole("radio", { name: "omp" }))
    await user.keyboard("{Escape}")
    await user.click(screen.getByRole("button", { name: "Fix default" }))
    await user.click(screen.getByRole("button", { name: "Fix default" }))

    expect(mutateAsync).toHaveBeenCalledWith({
      command: "release",
      args: ["fix-default", "proj", "--action", "both", "--run-mode", "preview", "--harness", "omp"],
      confirm: true,
    })
  })
})

function renderCard(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <ConfirmProvider>
          <ReleaseCard />
        </ConfirmProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe("ReleaseRow Pull button", () => {
  it("pulls remote changes when local repo is clean and invalidates queries", async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidateSpy = vi.spyOn(client, "invalidateQueries")
    mockRepos.value = [{ name: "proj", path: "/repos/proj", release_branch: "release" }]
    mockStatuses.value = { proj: { ...STATUS, dirty: false, dirty_files: [] } }
    renderCard(client)

    const pullBtn = screen.getByRole("button", { name: "Pull proj from remote" })
    expect(pullBtn).toBeEnabled()
    await user.click(pullBtn)

    expect(mutateAsync).toHaveBeenCalledWith({
      command: "repo",
      args: ["pull", "proj"],
      confirm: false,
    })
    expect(mockRun).toHaveBeenCalledWith("run-1")
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["release", "proj"] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["repos"] })
  })

  it("disables Pull with (!) tooltip when local repo has uncommitted changes", () => {
    mockRepos.value = [{ name: "proj", path: "/repos/proj", release_branch: "release" }]
    mockStatuses.value = { proj: { ...STATUS, dirty: true, dirty_files: ["M README.md", "?? tmp.txt"] } }
    renderCard()

    const pullBtn = screen.getByRole("button", { name: "Pull proj from remote" })
    expect(pullBtn).toBeDisabled()
    expect(pullBtn.getAttribute("title")).toContain("uncommitted changes")
    expect(pullBtn.getAttribute("title")).toContain("M README.md")
  })

  it("hides Pull button when has_remote_diff is false and shows it when true", () => {
    mockRepos.value = [{ name: "proj", path: "/repos/proj", release_branch: "release" }]
    mockStatuses.value = { proj: { ...STATUS, has_remote_diff: false } }
    const { rerender } = renderCard()
    expect(screen.queryByRole("button", { name: "Pull proj from remote" })).toBeNull()

    mockStatuses.value = { proj: { ...STATUS, has_remote_diff: true } }
    rerender(
      <MemoryRouter>
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <ConfirmProvider>
            <ReleaseCard />
          </ConfirmProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    )
    expect(screen.getByRole("button", { name: "Pull proj from remote" })).toBeInTheDocument()
  })

  it("toasts the old branch when the pull auto-switched to the default branch", async () => {
    const user = userEvent.setup()
    mockRepos.value = [{ name: "proj", path: "/repos/proj", release_branch: "release" }]
    mockStatuses.value = { proj: { ...STATUS, dirty: false, dirty_files: [] } }
    mockRun.mockResolvedValueOnce({
      state: "succeeded",
      lines: [
        { seq: 0, text: "pulled: proj" },
        { seq: 1, text: "branch: main" },
        { seq: 2, text: "switched_from: feat/old-branch" },
      ],
    })
    renderCard()

    await user.click(screen.getByRole("button", { name: "Pull proj from remote" }))

    expect(mockToastSuccess).toHaveBeenCalledWith(
      "Pulled proj from remote (switched from feat/old-branch to main)",
    )
  })
})

describe("ReleaseCard manual Refresh", () => {
  it("renders Refresh button on Ready to release header", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter>
        <QueryClientProvider client={qc}>
          <ConfirmProvider>
            <ReleaseCard />
          </ConfirmProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    )
    expect(screen.getByRole("button", { name: /Refresh/i })).toBeInTheDocument()
  })
})
