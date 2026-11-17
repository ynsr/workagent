import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"
import { HarnessSelect } from "@/components/HarnessSelect"

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

vi.mock("@/lib/queries", () => ({
  useConfig: () => ({ data: mockConfig.data }),
}))

afterEach(cleanup)

describe("HarnessSelect", () => {
  it("renders segmented variant with default option and availability", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<HarnessSelect idPrefix="test" value="" onChange={onChange} />)

    const defaultBtn = screen.getByRole("radio", { name: "Default (agy)" })
    expect(defaultBtn).toHaveAttribute("aria-checked", "true")
    expect(screen.getByRole("radio", { name: "agy" })).toBeInTheDocument()
    const ompBtn = screen.getByRole("radio", { name: "omp (missing)" })
    expect(ompBtn).toHaveAttribute("title", "omp not found on PATH")

    await user.click(ompBtn)
    expect(onChange).toHaveBeenCalledWith("omp")
  })

  it("renders select variant and respects allowDefault=false", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <HarnessSelect
        idPrefix="sel"
        variant="select"
        allowDefault={false}
        value="agy"
        onChange={onChange}
      />,
    )

    const select = screen.getByLabelText<HTMLSelectElement>("Harness")
    expect(select.tagName).toBe("SELECT")
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["agy", "omp"])
    expect(select.value).toBe("agy")

    await user.selectOptions(select, "omp")
    expect(onChange).toHaveBeenCalledWith("omp")
  })
})
