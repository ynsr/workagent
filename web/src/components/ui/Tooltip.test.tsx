import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { Button } from "@/components/ui/button"
import { Pressable } from "@/components/ui/pressable"
import { Tooltip } from "@/components/ui/tooltip"

afterEach(() => cleanup())

describe("ui primitives", () => {
  it("Tooltip keeps the trigger reachable and named", () => {
    render(
      <Tooltip label="Sync">
        <Button aria-label="Sync" size="icon-sm" variant="quiet" />
      </Tooltip>,
    )
    expect(screen.getByRole("button", { name: "Sync" })).toBeInTheDocument()
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
