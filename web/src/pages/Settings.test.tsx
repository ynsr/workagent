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
  queryKeys: { config: ["config"] },
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
  it("renders segmented harness selector without Default option and saves via config set", async () => {
    const user = userEvent.setup()
    renderSettings()

    expect(screen.getByText("Default AI Harness")).toBeInTheDocument()
    expect(screen.queryByRole("radio", { name: /^Default/ })).not.toBeInTheDocument()
    expect(screen.getByRole("radio", { name: "agy" })).toHaveAttribute("aria-checked", "true")

    await user.click(screen.getByRole("radio", { name: "omp" }))

    expect(mutateAsync).toHaveBeenCalledWith({
      command: "config",
      args: ["set", "default-harness", "omp"],
      confirm: false,
    })
  })
})
