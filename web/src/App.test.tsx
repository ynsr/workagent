import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter, useLocation } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
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
        <App />
        <LocationProbe />
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
