import { afterEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { Button } from "@/components/ui/button"
import { Pressable } from "@/components/ui/pressable"
import { Tooltip, TooltipProvider } from "@/components/ui/tooltip"

afterEach(() => {
  vi.useRealTimers()
  cleanup()
})

describe("ui primitives", () => {
  it("Tooltip keeps the trigger reachable and named (standalone fallback)", () => {
    render(
      <Tooltip label="Sync">
        <Button aria-label="Sync" size="icon-sm" variant="quiet" />
      </Tooltip>,
    )
    expect(screen.getByRole("button", { name: "Sync" })).toBeInTheDocument()
  })

  it("TooltipProvider shares skipDelayDuration across adjacent tooltips", () => {
    vi.useFakeTimers()
    render(
      <TooltipProvider delayDuration={400} skipDelayDuration={300}>
        <Tooltip label="First tip">
          <Button aria-label="First" size="icon-sm" variant="quiet" />
        </Tooltip>
        <Tooltip label="Second tip">
          <Button aria-label="Second" size="icon-sm" variant="quiet" />
        </Tooltip>
      </TooltipProvider>,
    )

    const first = screen.getByRole("button", { name: "First" })
    const second = screen.getByRole("button", { name: "Second" })

    // First hover waits for delayDuration (400ms)
    fireEvent.pointerMove(first, { pointerType: "mouse" })
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(400)
    })
    expect(screen.getByRole("tooltip")).toHaveTextContent("First tip")

    // Close first and move to second within skipDelayDuration (300ms): opens immediately
    fireEvent.keyDown(document, { key: "Escape" })
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(100)
    })
    fireEvent.pointerMove(second, { pointerType: "mouse" })
    expect(screen.getByRole("tooltip")).toHaveTextContent("Second tip")

    // Close second and wait past skipDelayDuration (350ms > 300ms): delayDuration applies again
    fireEvent.keyDown(document, { key: "Escape" })
    act(() => {
      vi.advanceTimersByTime(350)
    })
    fireEvent.pointerMove(first, { pointerType: "mouse" })
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument()
  })

  it("Pressable is a type=button with no press scale", () => {
    render(<Pressable>Row</Pressable>)
    const el = screen.getByRole("button", { name: "Row" })
    expect(el).toHaveAttribute("type", "button")
    expect(el.className).not.toMatch(/scale|active:/)
  })

  it("ghost-family variants use the growing pill and quiet size is 28px", () => {
    render(<Button variant="quiet" size="icon-sm" aria-label="q" />)
    const cls = screen.getByRole("button", { name: "q" }).className
    expect(cls).toMatch(/before:inset-1/)
    expect(cls).toMatch(/size-7/)
  })
})
