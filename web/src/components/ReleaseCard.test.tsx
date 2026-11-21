import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { FixDefaultDialog } from "@/components/ReleaseCard"
import { ConfirmProvider } from "@/lib/confirm"
import type { ReleaseStatus } from "@/lib/api"

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

vi.mock("@/lib/queries", () => ({
  queryKeys: { harnessOptions: (name: string) => ["harnesses", "options", name] },
  useConfig: () => ({ data: mockConfig }),
  useCreateRun: () => ({ mutateAsync }),
  useRepos: () => ({ data: [] }),
  useReleaseStatus: () => ({ data: undefined }),
  useReleaseStatuses: () => [],
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
})

afterEach(cleanup)

describe("FixDefaultDialog harness", () => {
  it("renders HarnessSelect and emits --harness in release fix-default args", async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.click(screen.getByRole("radio", { name: "omp" }))
    await user.click(screen.getByRole("button", { name: "Fix default" }))
    await user.click(screen.getByRole("button", { name: "Fix default" }))

    expect(mutateAsync).toHaveBeenCalledWith({
      command: "release",
      args: ["fix-default", "proj", "--action", "both", "--run-mode", "preview", "--harness", "omp"],
      confirm: true,
    })
  })
})
