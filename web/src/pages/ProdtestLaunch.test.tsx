import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ConfirmProvider } from "@/lib/confirm"
import { ProdtestLaunch } from "@/pages/ProdtestLaunch"

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
  useLinks: () => ({ data: { trackers: {}, worktrees: { "jira:IPG-10": { repo: "proj" } } } }),
  useRepos: () => ({ data: [{ name: "proj", path: "/repos/proj" }] }),
}))

function renderProdtestLaunch(initialEntry = "/prodtest-launch") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <QueryClientProvider client={client}>
        <ConfirmProvider>
          <ProdtestLaunch />
        </ConfirmProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  localStorage.clear()
  mutateAsync.mockClear()
})

afterEach(() => {
  cleanup()
})

describe("ProdtestLaunch harness integration", () => {
  it("emits --harness on prodtest plan", async () => {
    const user = userEvent.setup()
    renderProdtestLaunch("/prodtest-launch?ref=IPG-10&prodtestAction=plan")

    await user.click(screen.getByRole("radio", { name: "omp" }))
    await user.click(screen.getByRole("button", { name: "Plan prodtest" }))
    await user.click(screen.getByRole("button", { name: "Launch" }))

    expect(mutateAsync).toHaveBeenCalledWith({
      command: "prodtest",
      args: ["plan", "IPG-10", "--harness", "omp"],
      confirm: true,
    })
  })

  it("emits --harness on prodtest run --all", async () => {
    const user = userEvent.setup()
    renderProdtestLaunch("/prodtest-launch")

    await user.click(screen.getByRole("radio", { name: "agy" }))
    await user.click(screen.getByRole("button", { name: "Run all prodtests" }))
    await user.click(screen.getByRole("button", { name: "Launch" }))

    expect(mutateAsync).toHaveBeenCalledWith({
      command: "prodtest",
      args: ["run", "--all", "--harness", "agy"],
      confirm: true,
    })
  })
})
