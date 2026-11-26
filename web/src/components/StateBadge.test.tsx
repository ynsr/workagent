import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { CiBadge, IssueStatusBadge, RunStateBadge } from "@/components/StateBadge"

afterEach(() => cleanup())

describe("status glyphs (design/02 section 2.7)", () => {
  it("renders run state as plain label with no border or fill", () => {
    const { container } = render(<RunStateBadge state="needs_input" />)
    expect(screen.getByText("Needs input")).toBeInTheDocument()
    expect(container.innerHTML).not.toMatch(/border|bg-/)
    expect(container.querySelector("svg")).not.toBeNull()
  })

  it("gives every CI state a distinct accessible name", () => {
    for (const ci of ["success", "failure", "running", null]) {
      const { container, unmount } = render(<CiBadge ci={ci} />)
      expect(container.textContent).toMatch(/CI: (success|failure|running)|No CI status/)
      unmount()
    }
  })

  it("renders nothing for an unknown issue status", () => {
    const { container } = render(<IssueStatusBadge status="" />)
    expect(container.firstChild).toBeNull()
  })
})
