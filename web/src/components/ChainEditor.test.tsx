import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"
import { ChainEditor, startRepoState, validateChain } from "@/components/ChainEditor"
import type { FlowStep } from "@/lib/api"
import { summarizeExecution } from "@/lib/flowExecution"

vi.mock("@/lib/queries", () => ({
  useRepos: () => ({ data: [] }),
  useConfig: () => ({
    data: {
      default_harness: "agy",
      supported_harnesses: ["agy", "omp"],
      harnesses: [
        { name: "agy", label: "Antigravity (agy-guard)", binary: "agy-guard", available: true },
        { name: "omp", label: "OMP (omp)", binary: "omp", available: true },
      ],
    },
  }),
}))

const ACTIONS = [
  { label: "lint", name: "Lint" },
  { label: "typecheck", name: "Typecheck" },
]

describe("validateChain start steps", () => {
  it("allows a start step after the first step", () => {
    const steps: FlowStep[] = [{ kind: "sync", config: {} }, { kind: "start", config: {} }]
    expect(validateChain(steps)).toEqual([])
  })

  it("allows two start steps", () => {
    const steps: FlowStep[] = [
      { kind: "start", config: {} },
      { kind: "start", config: { plan: true } },
    ]
    expect(validateChain(steps)).toEqual([])
  })

  it("rejects a start step after cleanup", () => {
    const steps: FlowStep[] = [
      { kind: "start", config: {} },
      { kind: "cleanup", config: {} },
      { kind: "start", config: {} },
    ]
    expect(validateChain(steps)).toEqual([
      "Step 3: 'start' steps must come before 'cleanup'.",
    ])
  })
})

describe("startRepoState", () => {
  it("follows a late start step instead of step 1", () => {
    expect(startRepoState([
      { kind: "sync", config: {} },
      { kind: "start", config: {} },
    ])).toBe("missing")
    expect(startRepoState([
      { kind: "sync", config: {} },
      { kind: "start", config: { repo: "proj" } },
    ])).toBe("ok")
    expect(startRepoState([{ kind: "sync", config: {} }])).toBe("ok")
  })
})

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

describe("ChainEditor plan checkbox", () => {
  it("toggles plan through onChange for start steps", async () => {
    const user = userEvent.setup()
    let next: FlowStep[] = []
    const steps: FlowStep[] = [{ kind: "start", config: {} }]
    render(
      <ChainEditor
        mode="edit"
        steps={steps}
        onChange={(s) => { next = s }}
        knownActions={[]}
      />,
    )
    const box = screen.getByLabelText(/Plan mode/)
    expect(box).not.toBeChecked()
    await user.click(box)
    expect(next[0]?.config?.plan).toBe(true)
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

function renderChain(steps: FlowStep[], extra?: { onChange?: (s: FlowStep[]) => void; showExecutionMode?: boolean }) {
  let next: FlowStep[] = []
  const { container, unmount } = render(
    <ChainEditor
      mode="edit"
      steps={steps}
      onChange={(s) => { next = s; extra?.onChange?.(s) }}
      knownActions={[]}
      {...(extra?.showExecutionMode !== undefined ? { showExecutionMode: extra.showExecutionMode } : {})}
    />,
  )
  return { container, unmount, next: () => next }
}

describe("ChainEditor execution mode", () => {
  it("toggles parallel on a review step", async () => {
    const user = userEvent.setup()
    const steps: FlowStep[] = [{ kind: "review", config: {} }]
    const { container, unmount, next } = renderChain(steps)
    await user.click(within(container).getByRole("radio", { name: "Step 1 parallel" }))
    expect(next()[0]?.execution_mode).toBe("parallel")
    unmount()
  })

  it("disables parallel on cleanup steps", () => {
    const steps: FlowStep[] = [{ kind: "cleanup", config: {} }]
    const { container, unmount } = renderChain(steps)
    expect(within(container).getByRole("radio", { name: "Step 1 parallel" })).toBeDisabled()
    unmount()
  })

  it("hides the toggle in single-worktree mode", () => {
    const steps: FlowStep[] = [{ kind: "review", config: {} }]
    const { container, unmount } = renderChain(steps, { showExecutionMode: false })
    expect(within(container).queryByRole("radiogroup", { name: "Step 1 execution mode" })).not.toBeInTheDocument()
    unmount()
  })
})
describe("ChainEditor execution bulk bar", () => {
  it("disables both bulk actions for forced-only steps", () => {
    const { unmount } = renderChain([{ kind: "start", config: {} }, { kind: "cleanup", config: {} }])
    expect(screen.getByRole("button", { name: "All parallel" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "All sequential" })).toBeDisabled()
    unmount()
  })

  it("sets every parallelizable step parallel, skipping forced kinds", async () => {
    const user = userEvent.setup()
    const steps: FlowStep[] = [
      { kind: "start", config: {} },
      { kind: "review", config: {} },
      { kind: "cleanup", config: {} },
    ]
    const { unmount, next } = renderChain(steps)
    await user.click(screen.getByRole("button", { name: "All parallel" }))
    expect(next()[1]?.execution_mode).toBe("parallel")
    expect(next()[0]?.execution_mode).toBeUndefined()
    expect(next()[2]?.execution_mode).toBeUndefined()
    expect(summarizeExecution(next()).runsParallel).toBe(true)
    unmount()
  })

  it("clears parallel back to the sequential default", async () => {
    const user = userEvent.setup()
    const steps: FlowStep[] = [
      { kind: "review", config: {}, execution_mode: "parallel" },
      { kind: "sync", config: {}, execution_mode: "parallel" },
    ]
    const seen: FlowStep[][] = []
    render(
      <ChainEditor
        mode="edit"
        steps={steps}
        onChange={(s) => { seen.push(s) }}
        knownActions={[]}
      />,
    )
    await user.click(screen.getByRole("button", { name: "All sequential" }))
    expect(seen.at(-1)?.every((s) => s.execution_mode === undefined)).toBe(true)
  })
})

describe("ChainEditor execution summary", () => {
  it("reports one-worktree-at-a-time when any step is sequential", () => {
    const steps: FlowStep[] = [
      { kind: "review", config: {}, execution_mode: "parallel" },
      { kind: "fix_comments", config: {} },
    ]
    const { container, unmount } = renderChain(steps)
    expect(within(container).getByRole("status")).toHaveTextContent("one worktree at a time")
    expect(within(container).getByRole("status")).toHaveTextContent("1 parallel · 1 sequential")
    unmount()
  })
})

describe("ChainEditor launch mode", () => {
  it("distinguishes chosen sequential mode from forced sequential mode", () => {
    const { container, unmount } = render(
      <ChainEditor mode="launch" steps={[{ kind: "review", config: {} }, { kind: "cleanup", config: {} }]} knownActions={[]} />,
    )
    expect(within(container).getByTitle("Runs one worktree at a time")).toHaveTextContent("sequential")
    expect(within(container).getByTitle("cleanup steps always run sequentially across worktrees")).toHaveTextContent("sequential")
    unmount()
  })

  it("shows a read-only execution badge per step", () => {
    const steps: FlowStep[] = [{ kind: "review", config: {}, execution_mode: "parallel" }]
    const { container, unmount } = render(
      <ChainEditor mode="launch" steps={steps} knownActions={[]} />,
    )
    expect(within(container).getByText("∥ parallel")).toBeInTheDocument()
    expect(within(container).queryByRole("radiogroup")).not.toBeInTheDocument()
    unmount()
  })
})

describe("ChainEditor step harness", () => {
  it("renders Harness select on start/sync/prodtest_plan and omits it on cleanup/release", async () => {
    const user = userEvent.setup()
    for (const kind of ["start", "review", "fix_comments", "sync", "action", "prodtest_plan"]) {
      let next: FlowStep[] = []
      const { container, unmount } = render(
        <ChainEditor
          mode="edit"
          steps={[{ kind, config: {} }]}
          onChange={(s) => { next = s }}
          knownActions={ACTIONS}
        />,
      )
      const select = within(container).getByLabelText<HTMLSelectElement>("Harness")
      await user.selectOptions(select, "omp")
      expect(next[0]?.config?.harness).toBe("omp")
      unmount()
    }
    for (const kind of ["cleanup", "release"]) {
      const { container, unmount } = render(
        <ChainEditor
          mode="edit"
          steps={[{ kind, config: {} }]}
          onChange={() => undefined}
          knownActions={[]}
        />,
      )
      expect(within(container).queryByLabelText("Harness")).not.toBeInTheDocument()
      unmount()
    }
  })

  it("omits config.harness when reset to default", async () => {
    const user = userEvent.setup()
    let next: FlowStep[] = []
    const { container } = render(
      <ChainEditor
        mode="edit"
        steps={[{ kind: "sync", config: { harness: "omp" } }]}
        onChange={(s) => { next = s }}
        knownActions={[]}
      />,
    )
    const select = within(container).getByLabelText<HTMLSelectElement>("Harness")
    expect(select.value).toBe("omp")
    await user.selectOptions(select, "")
    expect(next[0]?.config).toEqual({})
  })
})
