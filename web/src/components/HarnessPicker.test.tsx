import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"
import { HarnessPicker } from "@/components/HarnessPicker"

const mockConfig = vi.hoisted(() => ({
  data: {
    default_harness: "agy",
    supported_harnesses: ["agy", "omp"],
    harnesses: [
      { name: "agy", label: "Antigravity (agy-guard)", binary: "agy-guard", available: true },
      { name: "omp", label: "OMP (omp)", binary: "omp", available: false },
    ],
  },
}))

const mockOptions = vi.hoisted(() => ({
  data: {
    supported: true,
    models: [
      { id: "claude-sonnet-5.5", name: "Claude Sonnet 5.5" },
      { id: "gemini-4-hydrogen", name: "Gemini 4 Hydrogen" },
    ],
    efforts: [
      { id: "low", name: "Low" },
      { id: "medium", name: "Medium" },
      { id: "high", name: "High" },
    ],
    current_model: "claude-sonnet-5.5",
    current_effort: "medium",
    warning: "",
  },
  isPending: false,
  isError: false,
}))

vi.mock("@/lib/queries", () => ({
  useConfig: () => ({ data: mockConfig.data }),
}))

vi.mock("@/hooks/useHarnessOptions", () => ({
  useHarnessOptions: () => mockOptions,
}))

function renderPicker(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

afterEach(cleanup)

describe("HarnessPicker", () => {
  it("formats compact trigger with active harness, model, and effort", () => {
    renderPicker(
      <HarnessPicker
        idPrefix="p"
        harness="agy"
        model="claude-sonnet-5.5"
        effort="medium"
        onHarnessChange={vi.fn()}
      />,
    )
    expect(screen.getByRole("button", { name: "Harness, model, and effort" })).toHaveTextContent(
      "agy · Claude Sonnet 5.5 · Medium",
    )
  })

  it("shows missing harness tooltip and calls onHarnessChange on harness switch", async () => {
    const user = userEvent.setup()
    const onHarness = vi.fn()
    renderPicker(
      <HarnessPicker
        idPrefix="p"
        harness=""
        model="claude-sonnet-5.5"
        effort="medium"
        onHarnessChange={onHarness}
      />,
    )
    const trigger = screen.getByRole("button", { name: "Harness, model, and effort" })
    expect(trigger).toHaveTextContent("Default (agy) · Claude Sonnet 5.5 · Medium")

    await user.click(trigger)
    const ompRadio = screen.getByRole("radio", { name: "omp (missing)" })
    expect(ompRadio).toHaveAttribute("title", "omp not found on PATH")
    await user.click(ompRadio)
    expect(onHarness).toHaveBeenCalledWith("omp")
  })

  it("filters models with search input and supports keyboard navigation", async () => {
    const user = userEvent.setup()
    const onModel = vi.fn()
    renderPicker(
      <HarnessPicker
        idPrefix="p"
        harness="agy"
        onHarnessChange={vi.fn()}
        onModelChange={onModel}
      />,
    )
    await user.click(screen.getByRole("button", { name: "Harness, model, and effort" }))
    const search = screen.getByLabelText("Search models")
    await user.type(search, "gemini")

    expect(screen.queryByRole("option", { name: /Claude Sonnet/ })).not.toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Gemini 4 Hydrogen" })).toBeInTheDocument()

    await user.keyboard("{Enter}")
    expect(onModel).toHaveBeenCalledWith("gemini-4-hydrogen")
  })

  it("shows empty search message and supports progressive disclosure for effort", async () => {
    const user = userEvent.setup()
    const onEffort = vi.fn()
    renderPicker(
      <HarnessPicker
        idPrefix="p"
        harness="agy"
        onHarnessChange={vi.fn()}
        onEffortChange={onEffort}
      />,
    )
    await user.click(screen.getByRole("button", { name: "Harness, model, and effort" }))
    await user.type(screen.getByLabelText("Search models"), "nonexistent")
    expect(screen.getByText("No matching models")).toBeInTheDocument()

    expect(screen.queryByRole("listbox", { name: "Effort options" })).not.toBeInTheDocument()
    const effortRow = screen.getByRole("button", { name: "Effort" })
    await user.click(effortRow)
    expect(screen.getByRole("listbox", { name: "Effort options" })).toBeInTheDocument()
    await user.click(screen.getByRole("option", { name: "High" }))
    expect(onEffort).toHaveBeenCalledWith("high")
  })

  it("supports harnessOnly mode without model/effort sections", async () => {
    const user = userEvent.setup()
    const onHarness = vi.fn()
    renderPicker(
      <HarnessPicker
        idPrefix="s"
        harness="agy"
        allowDefault={false}
        harnessOnly
        onHarnessChange={onHarness}
      />,
    )
    const trigger = screen.getByRole("button", { name: "Harness" })
    expect(trigger).toHaveTextContent("agy")
    await user.click(trigger)
    expect(screen.queryByLabelText("Search models")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Effort" })).not.toBeInTheDocument()
    await user.click(screen.getByRole("radio", { name: "omp (missing)" }))
    expect(onHarness).toHaveBeenCalledWith("omp")
  })
})
