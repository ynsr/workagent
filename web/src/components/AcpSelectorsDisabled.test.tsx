import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
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
      <HarnessPicker idPrefix="d" harness="agy" onHarnessChange={vi.fn()} />,
    )
    await user.click(screen.getByRole("button", { name: /^Harness, model, and effort/ }))
    const modelBtn = screen.getByRole("button", { name: "Model" })
    expect(modelBtn).toHaveAttribute("aria-disabled", "true")
    expect(modelBtn).not.toHaveAttribute("title")
    expect(modelBtn.parentElement).not.toHaveAttribute("title")
    modelBtn.focus()
    const modelTooltip = await screen.findByRole("tooltip")
    expect(modelTooltip).toHaveTextContent(/Model discovery failed/)
    expect(modelBtn).toHaveAttribute("aria-describedby", modelTooltip.id)

    fireEvent.blur(modelBtn)
    const effortBtn = screen.getByRole("button", { name: "Effort" })
    expect(effortBtn).toHaveAttribute("aria-disabled", "true")
    expect(effortBtn).not.toHaveAttribute("title")
    effortBtn.focus()
    const effortTooltip = await screen.findByRole("tooltip")
    expect(effortTooltip).toHaveTextContent(/Effort discovery failed/)
    expect(effortBtn).toHaveAttribute("aria-describedby", effortTooltip.id)
  })
})
