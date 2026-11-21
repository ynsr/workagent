import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
import { EffortSelect, ModelSelect } from "@/components/ModelEffortSelect"

vi.mock("@/hooks/useHarnessOptions", () => ({
  useHarnessOptions: () => ({ data: null, isPending: false, isError: true }),
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

describe("ModelSelect/EffortSelect disabled state (issue #112)", () => {
  it("disables with tooltip when discovery failed", () => {
    renderWithClient(
      <>
        <ModelSelect idPrefix="d" harness="agy" value="" onChange={() => {}} />
        <EffortSelect idPrefix="d" harness="agy" value="" onChange={() => {}} />
      </>,
    )
    const model = screen.getByLabelText<HTMLSelectElement>("Model")
    expect(model).toBeDisabled()
    expect(model.closest("div")).toHaveAttribute("title", expect.stringContaining("failed"))
    expect(screen.getByLabelText<HTMLSelectElement>("Effort")).toBeDisabled()
  })
})
