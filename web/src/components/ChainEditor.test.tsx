import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"
import { ChainEditor } from "@/components/ChainEditor"
import type { FlowStep } from "@/lib/api"

vi.mock("@/lib/queries", () => ({
  useRepos: () => ({ data: [] }),
}))

const ACTIONS = [
  { label: "lint", name: "Lint" },
  { label: "typecheck", name: "Typecheck" },
]

describe("ChainEditor action dropdown", () => {
  it("lists known actions instead of an empty select", () => {
    const steps: FlowStep[] = [{ kind: "action", config: {} }]
    render(<ChainEditor mode="edit" steps={steps} onChange={() => undefined} knownActions={ACTIONS} />)
    const select = screen.getByLabelText("Action")
    expect(select).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Lint (lint)" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Typecheck (typecheck)" })).toBeInTheDocument()
  })

  it("shows each known action exactly once", () => {
    const steps: FlowStep[] = [{ kind: "action", config: {} }]
    render(<ChainEditor mode="edit" steps={steps} onChange={() => undefined} knownActions={ACTIONS} />)
    const select = screen.getByLabelText("Action")
    const options = Array.from(select.querySelectorAll("option")).map((o) => o.textContent)
    expect(options).toEqual(["Select an action…", "Lint (lint)", "Typecheck (typecheck)"])
  })
})

describe("ChainEditor fix-comments checkbox", () => {
  it("toggles through onChange in wizard (edit) mode", async () => {
    const user = userEvent.setup()
    let next: FlowStep[] = []
    const steps: FlowStep[] = [{ kind: "fix_comments", config: {} }]
    render(
      <ChainEditor
        mode="edit"
        steps={steps}
        onChange={(s) => { next = s }}
        knownActions={[]}
      />,
    )
    const box = screen.getByLabelText(/Use review session/)
    expect(box).not.toBeChecked()
    await user.click(box)
    expect(next[0]?.config?.use_review_session).toBe(true)
  })
})
