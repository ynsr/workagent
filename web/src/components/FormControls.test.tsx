import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { SegmentedControl } from "@/components/FormControls"

afterEach(() => cleanup())

describe("SegmentedControl", () => {
  it("renders hint via an accessible Radix Tooltip trigger instead of a native title on Label", async () => {
    render(
      <SegmentedControl
        idPrefix="cat"
        label="Category"
        hint="— worktree rows surface on Dashboard; message rows on Messages"
        options={[
          { value: "worktree", label: "worktree" },
          { value: "message", label: "message" },
        ]}
        value="worktree"
        onChange={vi.fn()}
      />,
    )

    const label = screen.getByText("Category")
    expect(label).not.toHaveAttribute("title")

    const helpTrigger = screen.getByRole("img", {
      name: "Category: worktree rows surface on Dashboard; message rows on Messages",
    })
    expect(helpTrigger).toHaveAttribute("tabindex", "0")
    expect(helpTrigger).not.toHaveAttribute("title")

    fireEvent.focus(helpTrigger)
    const tooltip = await screen.findByRole("tooltip")
    expect(tooltip).toHaveTextContent("worktree rows surface on Dashboard; message rows on Messages")
    expect(helpTrigger).toHaveAttribute("aria-describedby", tooltip.id)
  })
})
