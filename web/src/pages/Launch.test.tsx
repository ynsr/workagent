import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ConfirmProvider } from "@/lib/confirm"
import { Launch } from "@/pages/Launch"

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
  queryKeys: { issues: () => ["issues"], harnessOptions: (name: string) => ["harnesses", "options", name] },
  useConfig: () => ({ data: mockConfig }),
  useCreateRun: () => ({ mutateAsync }),
  useFlowTemplates: () => ({ data: { templates: [] } }),
  useLinks: () => ({ data: { trackers: {}, worktrees: { "jira:IPG-1": { repo: "proj" } } } }),
  useRepos: () => ({ data: [{ name: "proj", path: "/repos/proj" }] }),
}))

function renderLaunch(initialEntry = "/launch") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <QueryClientProvider client={client}>
        <ConfirmProvider>
          <Launch />
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

describe("Launch harness integration", () => {
  it("renders dynamic headless warning banner for agy vs omp", async () => {
    const user = userEvent.setup()
    renderLaunch("/launch?mode=start")

    await user.click(screen.getByRole("radio", { name: "Headless" }))
    expect(screen.getByText("agy-guard --dangerously-skip-permissions -p=…")).toBeInTheDocument()

    await user.click(screen.getByRole("radio", { name: "omp" }))
    expect(screen.getByText("omp -p --auto-approve")).toBeInTheDocument()

    await user.click(screen.getByRole("radio", { name: "Default (agy)" }))
    expect(screen.getByText("agy-guard --dangerously-skip-permissions -p=…")).toBeInTheDocument()
  })

  it("emits --harness on sync launch", async () => {
    const user = userEvent.setup()
    renderLaunch("/launch?mode=sync&ref=jira%3AIPG-1")

    await user.click(screen.getByRole("radio", { name: "omp" }))
    await user.click(screen.getByRole("button", { name: "Launch Sync" }))
    await user.click(screen.getByRole("button", { name: "Launch" }))

    expect(mutateAsync).toHaveBeenCalledWith({
      command: "sync",
      args: ["jira:IPG-1", "--harness", "omp"],
      confirm: true,
    })
  })

  it("emits --harness on adhoc launch", async () => {
    const user = userEvent.setup()
    renderLaunch("/launch?mode=adhoc")

    await user.type(screen.getByLabelText("Worktree refs (one per line)"), "jira:IPG-1")
    await user.type(screen.getByLabelText(/^Prompt/, { selector: "textarea" }), "summarize")
    await user.click(screen.getByRole("radio", { name: "agy" }))
    await user.click(screen.getByRole("button", { name: "Launch Adhoc Prompt" }))
    await user.click(screen.getByRole("button", { name: "Launch" }))

    expect(mutateAsync).toHaveBeenCalledWith({
      command: "adhoc",
      args: ["jira:IPG-1", "--prompt=summarize", "--harness", "agy", "--launch"],
      confirm: true,
    })
  })

  it("does not clobber sync prefs when mode is flow", async () => {
    const user = userEvent.setup()
    localStorage.setItem(
      "launch.prefs.v1",
      JSON.stringify({
        start: { runMode: "preview", startAction: "implement", base: "", harness: "agy" },
        review: { runMode: "preview", thenFix: false, fixAction: "review", useReviewSession: false, harness: "" },
        prodtest: { runMode: "preview", prodtestAction: "run", harness: "" },
        sync: { merge: true, harness: "omp" },
        adhoc: { runMode: "headless", harness: "" },
      }),
    )

    renderLaunch("/launch?mode=start")
    await user.click(screen.getByRole("tab", { name: "Flow" }))

    const storedAfterFlow = JSON.parse(localStorage.getItem("launch.prefs.v1") ?? "{}") as {
      sync?: { merge?: boolean; harness?: string; model?: string; effort?: string }
    }
    expect(storedAfterFlow.sync).toEqual({ merge: true, harness: "omp", model: "", effort: "" })

    await user.click(screen.getByRole("tab", { name: "Sync" }))
    expect(screen.getByRole("radio", { name: "omp" })).toHaveAttribute("aria-checked", "true")
    expect(screen.getByRole("checkbox", { name: /Merge locally/ })).toBeChecked()
  })

  it("does not clobber sync prefs when mounted directly on ?mode=flow", () => {
    localStorage.setItem(
      "launch.prefs.v1",
      JSON.stringify({
        start: { runMode: "preview", startAction: "implement", base: "", harness: "agy" },
        review: { runMode: "preview", thenFix: false, fixAction: "review", useReviewSession: false, harness: "" },
        prodtest: { runMode: "preview", prodtestAction: "run", harness: "" },
        sync: { merge: true, harness: "omp" },
        adhoc: { runMode: "headless", harness: "" },
      }),
    )

    renderLaunch("/launch?mode=flow")

    const stored = JSON.parse(localStorage.getItem("launch.prefs.v1") ?? "{}") as {
      sync?: { merge?: boolean; harness?: string }
    }
    // ?mode=flow mount writes nothing back — the legacy seed stays untouched.
    expect(stored.sync).toEqual({ merge: true, harness: "omp" })
  })
})
