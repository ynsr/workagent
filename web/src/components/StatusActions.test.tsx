import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
import { ActionIcon, RowActions } from "@/components/StatusActions"
import type { FlowTemplateRow, WorktreeMap } from "@/lib/api"
import type * as QueriesModule from "@/lib/queries"

const mockTemplates: FlowTemplateRow[] = [
  {
    id: 1,
    label: "ship-it",
    name: "Ship It",
    icon: "Rocket",
    steps: [{ kind: "sync" }],
    pre_execution: "",
    post_execution: "",
    dedicated_overlay: true,
    category: "worktree",
    created_at: "2026-01-01T00:00:00Z",
    modified_at: "2026-01-01T00:00:00Z",
  },
  {
    id: 2,
    label: "no-icon",
    name: "No Icon",
    icon: "",
    steps: [{ kind: "sync" }],
    pre_execution: "",
    post_execution: "",
    dedicated_overlay: true,
    category: "worktree",
    created_at: "2026-01-01T00:00:00Z",
    modified_at: "2026-01-01T00:00:00Z",
  },
]

vi.mock("@/lib/queries", async () => {
  const actual = await vi.importActual<typeof QueriesModule>("@/lib/queries")
  return {
    ...actual,
    useCreateRun: () => ({ mutateAsync: vi.fn() }),
    useFlowTemplates: () => ({
      data: { templates: mockTemplates },
      isPending: false,
      isError: false,
      error: null,
    }),
    useActions: () => ({
      data: { actions: [] },
      isPending: false,
      isError: false,
      error: null,
    }),
    useRuns: () => ({ data: [] }),
  }
})

afterEach(cleanup)

const entry: WorktreeMap[string] = {
  repo: "demo",
  worktree: "/tmp/wt",
  branch: "feat/demo",
  wt_valid: true,
  pr: "",
  ci: "",
  harness: "",
  issue_url: "",
}

describe("StatusActions DedicatedOverlay icons", () => {
  it("renders the flow template icon on the StatusTable row overlay button", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <RowActions
            worktreeKey="jira:DEMO-1"
            entry={entry}
            overlay
            showRuns={false}
            actions={{ onOpenWorktree: vi.fn(), onOpenRun: vi.fn() }}
          />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    const shipBtn = screen.getByRole("button", { name: "Run flow Ship It on jira:DEMO-1" })
    expect(shipBtn.querySelector("svg.lucide-rocket")).not.toBeNull()
    expect(shipBtn.querySelector("svg.lucide-workflow")).toBeNull()

    const fallbackBtn = screen.getByRole("button", { name: "Run flow No Icon on jira:DEMO-1" })
    expect(fallbackBtn.querySelector("svg.lucide-zap")).not.toBeNull()
  })

  it("keeps disabled ActionIcon focusable so its tooltip reason opens and guards clicks", async () => {
    const onClick = vi.fn()
    render(
      <ActionIcon title="Resume transcript: none yet" disabled onClick={onClick}>
        <span>H</span>
      </ActionIcon>,
    )
    const btn = screen.getByRole("button", { name: "Resume transcript: none yet" })
    expect(btn).toHaveAttribute("aria-disabled", "true")
    expect(btn).not.toBeDisabled()

    fireEvent.click(btn)
    expect(onClick).not.toHaveBeenCalled()

    fireEvent.focus(btn)
    const tooltip = await screen.findByRole("tooltip")
    expect(tooltip).toHaveTextContent("Resume transcript: none yet")
    expect(btn).toHaveAttribute("aria-describedby", tooltip.id)
  })
})
