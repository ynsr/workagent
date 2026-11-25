import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import userEvent from "@testing-library/user-event"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it, vi } from "vitest"
import { FlowWizard } from "@/components/FlowWizard"
import * as flowExecution from "@/lib/flowExecution"
import type { FlowTemplateRow } from "@/lib/api"

const { mutateAsync } = vi.hoisted(() => ({ mutateAsync: vi.fn(async () => ({ run_id: "run-1" })) }))
vi.mock("@/lib/queries", () => ({
  queryKeys: { harnessOptions: (name: string) => ["harnesses", "options", name] },
  useActions: () => ({
    data: { actions: [{ label: "lint", name: "Lint" }, { label: "typecheck", name: "Typecheck" }] },
  }),
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
  useCreateRun: () => ({ mutateAsync }),
  useRepos: () => ({ data: [] }),
}))

const TEMPLATE = {
  name: "ship",
  label: "ship",
  steps: [
    { kind: "start", config: { repo: "proj" } },
    { kind: "action", config: {}, action_label: "lint" },
  ],
} as unknown as FlowTemplateRow

function renderWizard(template: FlowTemplateRow = TEMPLATE, refs: string[] = ["jira:IPG-1"], onClose: () => void = vi.fn()) {
  const client = new QueryClient()
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <FlowWizard template={template} refs={refs} onClose={onClose} />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe("FlowWizard", () => {
  it("populates the custom-action dropdown from useActions", () => {
    renderWizard()
    expect(screen.getByRole("option", { name: "Lint (lint)" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Typecheck (typecheck)" })).toBeInTheDocument()
  })
})

describe("FlowWizard footer groups", () => {
  it("renders Save, Save as, Reset buttons alongside Run flow", () => {
    renderWizard()
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Save as" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Reset" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Run flow" })).toBeInTheDocument()
  })
})

describe("FlowWizard run", () => {
  it("closes the modal before the sequential queue settles", async () => {
    // Block the queue in `awaitRunSettled` so the test can observe close
    // order: with the old await-then-close code this times out instead.
    const spy = vi.spyOn(flowExecution, "awaitRunSettled").mockImplementation(() => {
      let resolve!: (value: boolean) => void
      const promise = new Promise<boolean>((r) => { resolve = r })
      const t = globalThis.setTimeout(() => resolve(true), 5000)
      return promise.finally(() => globalThis.clearTimeout(t))
    })
    try {
      const onClose = vi.fn()
      renderWizard(TEMPLATE, ["jira:IPG-1"], onClose)
      fireEvent.click(screen.getByRole("button", { name: "Run flow" }))
      await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    } finally {
      spy.mockRestore()
    }
  })
})
describe("FlowWizard resume controls", () => {
  it("renders a Fresh toggle for restart-instead-of-resume", () => {
    const { unmount } = renderWizard()
    const labels = screen.getAllByText("Fresh", { selector: "label" })
    expect(labels.length).toBeGreaterThanOrEqual(1)
    const last = labels[labels.length - 1]
    expect(last?.querySelector("input[type='checkbox']")).not.toBeNull()
    unmount()
  })
})
describe("FlowWizard fan-out summary", () => {
  it("announces concurrent fan-out for all-parallel multi-worktree chains", () => {
    const template = {
      ...TEMPLATE,
      steps: [
        { kind: "start", config: { repo: "proj" } },
        { kind: "review", config: {}, execution_mode: "parallel" },
      ],
    } as unknown as FlowTemplateRow
    const { unmount } = renderWizard(template, ["a", "b"])
    expect(screen.getByText(/Runs on 2 worktrees fan out at once/)).toBeInTheDocument()
    unmount()
  })

  it("hides the fan-out summary for a single worktree", () => {
    const { unmount } = renderWizard()
    expect(screen.queryByText(/Runs on \d+ worktrees/)).not.toBeInTheDocument()
    unmount()
  })
})

describe("FlowWizard start step constraints", () => {
  it("enables Run flow for two start steps and a start step after sync", () => {
    const template = {
      ...TEMPLATE,
      steps: [
        { kind: "sync", config: {} },
        { kind: "start", config: { repo: "proj" } },
        { kind: "start", config: { repo: "proj", plan: true } },
      ],
    } as unknown as FlowTemplateRow
    const { unmount } = renderWizard(template)
    expect(screen.getByRole("button", { name: "Run flow" })).toBeEnabled()
    expect(screen.queryByText(/At most one 'start'/)).not.toBeInTheDocument()
    expect(screen.queryByText(/'start' must be the first step/)).not.toBeInTheDocument()
    unmount()
  })

  it("forwards both start steps and their plan config to the run", async () => {
    mutateAsync.mockClear()
    cleanup()
    const template = {
      ...TEMPLATE,
      steps: [
        { kind: "sync", config: {} },
        { kind: "start", config: { repo: "proj" } },
        { kind: "start", config: { repo: "proj", plan: true } },
      ],
    } as unknown as FlowTemplateRow
    const { unmount } = renderWizard(template)
    const planBoxes = screen.getAllByLabelText(/Plan mode/)
    await userEvent.click(planBoxes[0]!)
    await userEvent.click(screen.getByRole("button", { name: "Run flow" }))
    await waitFor(() => expect(mutateAsync).toHaveBeenCalled())
    const [payload] = mutateAsync.mock.calls.at(-1) as unknown as [{ args: string[] }]
    const at = payload.args.indexOf("--steps")
    expect(at).toBeGreaterThan(-1)
    const steps = JSON.parse(payload.args[at + 1] ?? "[]") as { kind: string; config: Record<string, unknown> }[]
    expect(steps.map((s) => s.kind)).toEqual(["sync", "start", "start"])
    expect(steps[1]?.config).toEqual({ repo: "proj", plan: true })
    expect(steps[2]?.config).toEqual({ repo: "proj", plan: true })
    unmount()
  })

  it("forwards top-level harness selection to flow run args", async () => {
    mutateAsync.mockClear()
    cleanup()
    const user = userEvent.setup()
    const { unmount } = renderWizard()
    await user.click(screen.getAllByRole("button", { name: /^Harness, model, and effort/ })[0]!)
    expect(screen.getByRole("radio", { name: "Use template / default" })).toBeInTheDocument()
    await user.click(screen.getByRole("radio", { name: "omp" }))
    await user.keyboard("{Escape}")
    await user.click(screen.getByRole("button", { name: "Run flow" }))
    await waitFor(() => expect(mutateAsync).toHaveBeenCalled())
    expect(mutateAsync).toHaveBeenCalledWith({
      command: "flow",
      args: ["run", "ship", "jira:IPG-1", "--harness", "omp", "--yes"],
      confirm: true,
    })
    expect(mutateAsync).toHaveBeenCalledWith({
      command: "flow",
      args: ["template", "update", "ship", "--harness", "omp", "--model", "", "--effort", "", "--yes"],
      confirm: false,
    })
    unmount()
  })

  it("seeds harness, model, and effort from template defaults", () => {
    cleanup()
    const tpl = {
      ...TEMPLATE,
      default_harness: "omp",
      default_model: "m1",
      default_effort: "high",
    } as unknown as FlowTemplateRow
    const { unmount } = renderWizard(tpl)
    const trigger = screen.getAllByRole("button", { name: /^Harness, model, and effort/ })[0]!
    expect(trigger).toHaveTextContent("omp · m1 · high")
    unmount()
  })
})
