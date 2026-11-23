import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { CheckRow, FieldHelp } from "@/components/FieldHelp"

afterEach(() => cleanup())

describe("FieldHelp and CheckRow", () => {
  it("renders FieldHelp with an accessible, keyboard-focusable Radix Tooltip trigger and no native title", async () => {
    render(<FieldHelp label="Repository" flag="--repo" description="Registered repo name." />)
    expect(screen.getByText("Repository")).toBeInTheDocument()
    const trigger = screen.getByRole("img", { name: "Repository: --repo — Registered repo name." })
    expect(trigger).toHaveAttribute("tabindex", "0")
    expect(trigger).toHaveAttribute("data-state", "closed")
    expect(trigger).not.toHaveAttribute("title")

    fireEvent.focus(trigger)
    expect(await screen.findByRole("tooltip")).toHaveTextContent("--repo — Registered repo name.")
  })

  it("renders CheckRow with Tooltip trigger outside the label so clicking help does not toggle the checkbox", () => {
    const onChange = vi.fn()
    render(
      <CheckRow
        id="dry-run"
        checked={false}
        onChange={onChange}
        label="Dry run"
        flag="--dry-run"
        description="Preview without modifying state."
      />,
    )
    const trigger = screen.getByRole("img", { name: "Dry run: --dry-run — Preview without modifying state." })
    expect(trigger).toHaveAttribute("tabindex", "0")
    expect(trigger).toHaveAttribute("data-state", "closed")
    expect(trigger).not.toHaveAttribute("title")

    fireEvent.click(trigger)
    expect(onChange).not.toHaveBeenCalled()

    const checkbox = screen.getByRole("checkbox", { name: "Dry run" })
    expect(checkbox).not.toBeChecked()
    fireEvent.click(checkbox)
    expect(onChange).toHaveBeenCalledWith(true)
  })
})
