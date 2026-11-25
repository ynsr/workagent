import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
import { HarnessPicker } from "@/components/HarnessPicker"

vi.mock("@/hooks/useHarnessOptions", () => ({
  useHarnessOptions: () => ({ data: null, isPending: false, isError: true }),
}))

vi.mock("@/lib/queries", () => ({
  useConfig: () => ({ data: undefined }),
}))

function renderWithClient(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>{ui}</QueryClientProvider>
    </MemoryRouter>,
  )
}

afterEach(cleanup)

describe("HarnessPicker disabled state (issue #112 / #181)", () => {
  it("disables model and effort with tooltip when discovery failed", async () => {
    const user = userEvent.setup()
    renderWithClient(
      <HarnessPicker idPrefix="d" harness="agy" onHarnessChange={() => {}} />,
    )
    await user.click(screen.getByRole("button", { name: "Harness, model, and effort" }))
    const modelBtn = screen.getByRole("button", { name: "Model" })
    expect(modelBtn).toBeDisabled()
    expect(modelBtn.parentElement).toHaveAttribute("title", expect.stringContaining("failed"))
    const effortBtn = screen.getByRole("button", { name: "Effort" })
    expect(effortBtn).toBeDisabled()
    expect(effortBtn.parentElement).toHaveAttribute("title", expect.stringContaining("failed"))
  })
})
