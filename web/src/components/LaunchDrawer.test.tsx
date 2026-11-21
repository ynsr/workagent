import { useState } from "react"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"
import { LaunchDrawer } from "./LaunchDrawer"

afterEach(cleanup)

function Harness() {
  const [open, setOpen] = useState(false)
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>
        Open Drawer
      </button>
      <LaunchDrawer
        open={open}
        onClose={() => setOpen(false)}
        title="New Task"
        description="Launch a task"
      >
        <input aria-label="Task ref" />
        <button type="button">Submit</button>
      </LaunchDrawer>
    </div>
  )
}

describe("LaunchDrawer", () => {
  it("renders nothing when closed and opens with dialog semantics", async () => {
    const user = userEvent.setup()
    render(<Harness />)

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Open Drawer" }))

    const dialog = screen.getByRole("dialog", { name: "New Task" })
    expect(dialog).toBeInTheDocument()
    expect(dialog).toHaveAttribute("aria-modal", "true")
    expect(dialog.className).toContain("motion-reduce:transition-none")
  })

  it("closes on Escape and restores focus to the trigger button", async () => {
    const user = userEvent.setup()
    render(<Harness />)

    const trigger = screen.getByRole("button", { name: "Open Drawer" })
    await user.click(trigger)
    expect(screen.getByRole("dialog")).toBeInTheDocument()

    await user.keyboard("{Escape}")
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it("closes on backdrop click and Close button", async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(
      <LaunchDrawer open onClose={onClose} title="Test Drawer">
        <p>Body</p>
      </LaunchDrawer>,
    )

    await user.click(screen.getByTestId("launch-drawer-backdrop"))
    expect(onClose).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole("button", { name: "Close" }))
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it("traps Tab and Shift+Tab focus within the drawer", async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.click(screen.getByRole("button", { name: "Open Drawer" }))

    const closeBtn = screen.getByRole("button", { name: "Close" })
    const input = screen.getByLabelText("Task ref")
    const submitBtn = screen.getByRole("button", { name: "Submit" })

    // Initial focus lands on first focusable element (Close button)
    expect(closeBtn).toHaveFocus()

    await user.tab()
    expect(input).toHaveFocus()

    await user.tab()
    expect(submitBtn).toHaveFocus()

    // Wraps back to first
    await user.tab()
    expect(closeBtn).toHaveFocus()

    // Shift+Tab wraps back to last
    await user.tab({ shift: true })
    expect(submitBtn).toHaveFocus()
  })
})
