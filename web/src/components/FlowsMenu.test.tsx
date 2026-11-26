import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
import { FlowsMenu } from "@/components/FlowsMenu"
import type { FlowTemplateRow } from "@/lib/api"
import type * as QueriesModule from "@/lib/queries"

const mockTemplates: FlowTemplateRow[] = [
  {
    id: 1,
    label: "deploy-flow",
    name: "Deploy Flow",
    icon: "Rocket",
    steps: [{ kind: "start" }],
    pre_execution: "",
    post_execution: "",
    dedicated_top_bar: true,
    category: "worktree",
    created_at: "2026-01-01T00:00:00Z",
    modified_at: "2026-01-01T00:00:00Z",
  },
  {
    id: 2,
    label: "fallback-flow",
    name: "Fallback Flow",
    icon: "",
    steps: [{ kind: "start" }],
    pre_execution: "",
    post_execution: "",
    dedicated_top_bar: true,
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
  }
})

afterEach(cleanup)

function renderFlowsMenu() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <FlowsMenu selected={new Set()} />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe("FlowsMenu icons", () => {
  it("renders the flow template icon on the Dashboard top bar button", () => {
    renderFlowsMenu()

    const deployBtn = screen.getByRole("button", { name: "Deploy Flow" })
    expect(deployBtn.querySelector("svg.lucide-rocket")).not.toBeNull()
    expect(deployBtn.querySelector("svg.lucide-workflow")).toBeNull()

    const fallbackBtn = screen.getByRole("button", { name: "Fallback Flow" })
    expect(fallbackBtn.querySelector("svg.lucide-zap")).not.toBeNull()
  })

  it("renders the flow template icon in the Flows popover menu", async () => {
    const user = userEvent.setup()
    renderFlowsMenu()

    await user.click(screen.getByRole("button", { name: "Flows" }))

    const menuItems = screen.getAllByRole("button", { name: /Deploy Flow/ })
    const dropdownItem = menuItems.find((el) => el.getAttribute("type") === "button" && el.className.includes("w-full"))
    expect(dropdownItem).toBeDefined()
    expect(dropdownItem?.querySelector("svg.lucide-rocket")).not.toBeNull()
  })
})
