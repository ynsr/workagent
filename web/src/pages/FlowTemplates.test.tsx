import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it, vi } from "vitest"
import { ConfirmProvider } from "@/lib/confirm"
import { setVerbose } from "@/lib/settings"
import { FlowTemplates } from "@/pages/FlowTemplates"

const { mutateAsync, TEMPLATES } = vi.hoisted(() => ({
  mutateAsync: vi.fn(async () => ({ run_id: "run-1" })),
  TEMPLATES: [
    {
      id: 1,
      label: "ship",
      name: "Ship",
      steps: [
        { kind: "start", config: { repo: "proj" } },
        { kind: "review", config: {} },
      ],
    },
    {
      id: 2,
      label: "fix",
      name: "Fix",
      steps: [
        { kind: "start", config: { repo: "proj", plan: true } },
        { kind: "action", config: {}, action_label: "lint" },
      ],
    },
  ],
}))

vi.mock("@/lib/queries", () => ({
  queryKeys: { harnessOptions: (name: string) => ["harnesses", "options", name] },
  useFlowTemplates: () => ({
    data: { templates: TEMPLATES },
    isPending: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useActions: () => ({ data: { actions: [] } }),
  useConfig: () => ({
    data: {
      default_harness: "agy",
      supported_harnesses: ["agy", "omp"],
      harnesses: [
        { name: "agy", label: "Antigravity (agy-guard)", binary: "agy-guard", available: true },
        { name: "omp", label: "OMP (omp)", binary: "omp", available: true },
      ],
    },
  }),
  useCreateRun: () => ({ mutateAsync }),
  useRepos: () => ({ data: [] }),
}))

function renderPage() {
  const client = new QueryClient()
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <ConfirmProvider>
          <FlowTemplates />
        </ConfirmProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe("FlowTemplates compose", () => {
  it("keeps every source start step and pins the lineage note", async () => {
    const user = userEvent.setup()
    const { unmount } = renderPage()
    await user.click(screen.getByLabelText("Select Ship"))
    await user.click(screen.getByLabelText("Select Fix"))
    await user.click(screen.getByRole("button", { name: "Compose 2 selected" }))
    expect(screen.getByText("Composed from ship, fix.")).toBeInTheDocument()
    const kinds = [1, 2, 3, 4].map(
      (i) => (screen.getByLabelText(`Step ${i} kind`) as HTMLSelectElement).value,
    )
    expect(kinds).toEqual(["start", "review", "start", "action"])
    unmount()
  })
})

describe("FlowTemplates UI density", () => {
  it("gates GET /api/flow-templates behind verbose and groups trailing controls", () => {
    localStorage.clear()
    const { unmount } = renderPage()
    expect(screen.queryByText("GET /api/flow-templates")).not.toBeInTheDocument()
    expect(screen.getByRole("columnheader", { name: "Controls" })).toBeInTheDocument()
    expect(screen.queryByRole("columnheader", { name: "Order" })).not.toBeInTheDocument()
    expect(screen.queryByRole("columnheader", { name: "Protected" })).not.toBeInTheDocument()
    expect(screen.queryByRole("columnheader", { name: "Top" })).not.toBeInTheDocument()
    expect(screen.queryByRole("columnheader", { name: "Overlay" })).not.toBeInTheDocument()
    expect(screen.getByTitle("Move Ship up")).toBeInTheDocument()
    expect(screen.getByTitle("Protect Ship")).toBeInTheDocument()
    unmount()

    setVerbose(true)
    const { unmount: unmountVerbose } = renderPage()
    expect(screen.getByText("GET /api/flow-templates")).toBeInTheDocument()
    unmountVerbose()
    localStorage.clear()
  })
})
