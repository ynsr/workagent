import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
import { HarnessSelect } from "@/components/HarnessSelect"
import { EffortSelect, ModelSelect } from "@/components/ModelEffortSelect"
import { RunModeSelect } from "@/components/StartForm"

const mockConfig = vi.hoisted(() => ({
  data: {
    default_harness: "agy",
    supported_harnesses: ["agy", "omp"],
    harnesses: [
      { name: "agy", label: "Antigravity (agy-guard)", binary: "agy-guard", available: true },
      { name: "omp", label: "OMP (omp)", binary: "omp", available: true },
    ],
  },
}))

const optionsData = vi.hoisted(() => ({
  data: {
    supported: true,
    models: [{ id: "m1", name: "Model One" }],
    efforts: [{ id: "high", name: "High" }],
    current_model: "m1",
    current_effort: "high",
    warning: "",
  },
}))

vi.mock("@/lib/queries", () => ({
  useConfig: () => ({ data: mockConfig.data }),
}))

vi.mock("@/hooks/useHarnessOptions", () => ({
  useHarnessOptions: () => ({ data: optionsData.data, isPending: false, isError: false }),
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

describe("RunModeSelect (issue #112)", () => {
  it("offers all 4 modes including acp", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    renderWithClient(<RunModeSelect idPrefix="t" value="preview" onChange={onChange} />)
    expect(screen.getByRole("radio", { name: "Run ACP" })).toBeInTheDocument()
    await user.click(screen.getByRole("radio", { name: "Run ACP" }))
    expect(onChange).toHaveBeenCalledWith("acp")
  })
})

describe("HarnessSelect (issue #112)", () => {
  it("lists only omp and agy plus default", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    renderWithClient(<HarnessSelect idPrefix="t" value="" onChange={onChange} />)
    expect(screen.getByRole("radio", { name: "Default (agy)" })).toBeInTheDocument()
    expect(screen.getByRole("radio", { name: "omp" })).toBeInTheDocument()
    expect(screen.getByRole("radio", { name: "agy" })).toBeInTheDocument()
    expect(screen.queryByRole("radio", { name: /claude/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole("radio", { name: "omp" }))
    expect(onChange).toHaveBeenCalledWith("omp")
  })
})

describe("ModelSelect/EffortSelect (issue #112)", () => {
  it("renders discovered options with Default empty", async () => {
    const user = userEvent.setup()
    const onModel = vi.fn()
    const onEffort = vi.fn()
    renderWithClient(
      <>
        <ModelSelect idPrefix="t" harness="agy" value="" onChange={onModel} />
        <EffortSelect idPrefix="t" harness="agy" value="" onChange={onEffort} />
      </>,
    )
    const model = screen.getByLabelText<HTMLSelectElement>("Model")
    expect(model).not.toBeDisabled()
    expect(Array.from(model.options).map((o) => o.value)).toEqual(["", "m1"])
    await user.selectOptions(model, "m1")
    expect(onModel).toHaveBeenCalledWith("m1")

    const effort = screen.getByLabelText<HTMLSelectElement>("Effort")
    expect(Array.from(effort.options).map((o) => o.value)).toEqual(["", "high"])
    await user.selectOptions(effort, "high")
    expect(onEffort).toHaveBeenCalledWith("high")
  })
})
