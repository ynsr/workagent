import { render, screen, within } from "@testing-library/react"
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

describe("ChainEditor release selects", () => {
  it("shows Checks and Diff selects only for release steps", () => {
    const release: FlowStep[] = [{ kind: "release", config: {} }]
    const { unmount } = render(
      <ChainEditor mode="edit" steps={release} onChange={() => undefined} knownActions={[]} />,
    )
    expect(screen.getByLabelText("Checks")).toBeInTheDocument()
    expect(screen.getByLabelText("Diff")).toBeInTheDocument()
    unmount()
    const cleanup: FlowStep[] = [{ kind: "cleanup", config: {} }]
    render(
      <ChainEditor mode="edit" steps={cleanup} onChange={() => undefined} knownActions={[]} />,
    )
    expect(screen.queryByLabelText("Checks")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Diff")).not.toBeInTheDocument()
  })
})

describe("ChainEditor execution mode", () => {
  it("toggles parallel on a review step", async () => {
    const user = userEvent.setup()
    let next: FlowStep[] = []
    const steps: FlowStep[] = [{ kind: "review", config: {} }]
    const { container, unmount } = render(
      <ChainEditor
        mode="edit"
        steps={steps}
        onChange={(s) => { next = s }}
        knownActions={[]}
      />,
    )
    await user.click(within(container).getByRole("radio", { name: "Step 1 parallel" }))
    expect(next[0]?.execution_mode).toBe("parallel")
    unmount()
  })

  it("disables parallel on cleanup steps", () => {
    const steps: FlowStep[] = [{ kind: "cleanup", config: {} }]
    const { container, unmount } = render(
      <ChainEditor mode="edit" steps={steps} onChange={() => undefined} knownActions={[]} />,
    )
    expect(within(container).getByRole("radio", { name: "Step 1 parallel" })).toBeDisabled()
    unmount()
  })

  it("hides the toggle in single-worktree mode", () => {
    const steps: FlowStep[] = [{ kind: "review", config: {} }]
    const { container, unmount } = render(
      <ChainEditor
        mode="edit"
        steps={steps}
        onChange={() => undefined}
        knownActions={[]}
        showExecutionMode={false}
      />,
    )
    expect(within(container).queryByRole("radiogroup", { name: "Step 1 execution mode" })).not.toBeInTheDocument()
    unmount()
  })
})
