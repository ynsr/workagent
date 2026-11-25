import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ThemeProvider } from "@/lib/theme"
import { Settings } from "@/pages/Settings"

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
  queryKeys: {
    config: ["config"],
    harnessOptions: (name: string) => ["harnessOptions", name],
  },
  useConfig: () => ({ data: mockConfig }),
  useCreateRun: () => ({ mutateAsync }),
}))

function renderSettings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <Settings />
      </ThemeProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  localStorage.clear()
  mutateAsync.mockClear()
  mockConfig.default_harness = "agy"
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })
})

afterEach(cleanup)

describe("Settings Default AI Harness", () => {
  it("renders harness picker without Default option and saves via config set", async () => {
    const user = userEvent.setup()
    renderSettings()

    expect(screen.getByText("Default AI Harness")).toBeInTheDocument()
    const trigger = screen.getByRole("button", { name: /^Default harness/ })
    expect(trigger).toHaveTextContent("agy")
    await user.click(trigger)
    expect(screen.queryByRole("radio", { name: /^Default/ })).not.toBeInTheDocument()
    expect(screen.getByRole("radio", { name: "agy" })).toHaveAttribute("aria-checked", "true")

    await user.click(screen.getByRole("radio", { name: "omp" }))

    expect(mutateAsync).toHaveBeenCalledWith({
      command: "config",
      args: ["set", "default-harness", "omp"],
      confirm: false,
    })
  })

  it("updates cleanly when cfg.default_harness changes externally without reverting to stale optimistic state", async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const tree = () => (
      <QueryClientProvider client={client}>
        <ThemeProvider>
          <Settings />
        </ThemeProvider>
      </QueryClientProvider>
    )
    const { rerender } = render(tree())

    await user.click(screen.getByRole("button", { name: /^Default harness/ }))
    await user.click(screen.getByRole("radio", { name: "omp" }))
    expect(screen.getByRole("button", { name: /^Default harness/ })).toHaveTextContent("omp")

    // Server config settles to "omp".
    mockConfig.default_harness = "omp"
    rerender(tree())
    expect(screen.getByRole("button", { name: /^Default harness/ })).toHaveTextContent("omp")

    // External CLI change switches config back to "agy" while Settings stays mounted.
    mockConfig.default_harness = "agy"
    rerender(tree())
    expect(screen.getByRole("button", { name: /^Default harness/ })).toHaveTextContent("agy")

    // Clicking "omp" again must not be blocked by stale optimistic state.
    mutateAsync.mockClear()
    await user.click(screen.getByRole("button", { name: /^Default harness/ }))
    await user.click(screen.getByRole("radio", { name: "omp" }))
    expect(mutateAsync).toHaveBeenCalledWith({
      command: "config",
      args: ["set", "default-harness", "omp"],
      confirm: false,
    })
  })
})
