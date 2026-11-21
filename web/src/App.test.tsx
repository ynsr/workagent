import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter, useLocation } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
import { ConfirmProvider } from "@/lib/confirm"
import App from "./App"
import type * as QueriesModule from "@/lib/queries"

/** Every data hook resolves to an empty, settled read: the routes and the
 * layout are under test here, not page contents. */
const { emptyQuery } = vi.hoisted(() => ({
  emptyQuery: {
    data: undefined,
    isPending: true,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  },
}))

afterEach(cleanup)

vi.mock("@/lib/queries", async () => {
  const actual = await vi.importActual<typeof QueriesModule>("@/lib/queries")
  const stubbed: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(actual)) {
    stubbed[name] = name.startsWith("use") ? () => emptyQuery : value
  }
  return stubbed
})

/** Reports the live URL so a redirect can be asserted, not just the page. */
function LocationProbe() {
  return <span data-testid="pathname">{useLocation().pathname}</span>
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={client}>
        <ConfirmProvider>
          <App />
          <LocationProbe />
        </ConfirmProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe("App sessions routes", () => {
  it("redirects the removed /sessions list to Runs", () => {
    renderAt("/sessions")
    expect(screen.getByRole("heading", { name: "Runs" })).toBeInTheDocument()
    expect(screen.getByTestId("pathname")).toHaveTextContent(/^\/runs$/)
  })

  it("redirects a /sessions/:sessionId link to Runs", () => {
    renderAt("/sessions/2026-01-01T00-00-00-000Z-1234")
    expect(screen.getByRole("heading", { name: "Runs" })).toBeInTheDocument()
    expect(screen.getByTestId("pathname")).toHaveTextContent(/^\/runs$/)
  })

  it("has no Sessions nav entry", () => {
    renderAt("/runs")
    expect(screen.queryByRole("link", { name: "Sessions" })).toBeNull()
  })
})

describe("App consolidated navigation and backward-compatible routes", () => {
  it("renders 7 sidebar destinations", () => {
    renderAt("/")
    const aside = document.querySelector("aside")
    expect(aside).not.toBeNull()
    const links = Array.from(aside!.querySelectorAll("a")).map((a) => a.textContent?.trim())
    expect(links).toEqual([
      "Dashboard",
      "Messages",
      "Runs & Flows",
      "Prodtests",
      "Automation Library",
      "Repos & Trackers",
      "Settings",
    ])
  })

  it("opens drawers on legacy /launch, /message-launch, and /prodtest-launch routes", () => {
    renderAt("/launch")
    expect(screen.getByRole("dialog", { name: "New Task" })).toBeInTheDocument()
    cleanup()

    renderAt("/message-launch")
    expect(screen.getByRole("dialog", { name: "Run on Message" })).toBeInTheDocument()
    cleanup()

    renderAt("/prodtest-launch")
    expect(screen.getByRole("dialog", { name: "Plan / Run Prodtest" })).toBeInTheDocument()
  })

  it("pre-selects the matching tab on legacy routes", () => {
    renderAt("/flows")
    expect(screen.getByRole("tab", { name: "Flows" })).toHaveAttribute("aria-selected", "true")
    cleanup()

    renderAt("/flow-templates")
    expect(screen.getByRole("tab", { name: "Flow Templates" })).toHaveAttribute("aria-selected", "true")
    cleanup()

    renderAt("/actions")
    expect(screen.getByRole("tab", { name: "Actions" })).toHaveAttribute("aria-selected", "true")
    cleanup()

    renderAt("/trackers")
    expect(screen.getByRole("tab", { name: "Trackers" })).toHaveAttribute("aria-selected", "true")
    cleanup()

    renderAt("/links")
    expect(screen.getByRole("tab", { name: "Mappings" })).toHaveAttribute("aria-selected", "true")
    cleanup()

    renderAt("/doctor")
    expect(screen.getByRole("tab", { name: "Doctor" })).toHaveAttribute("aria-selected", "true")
  })
})
