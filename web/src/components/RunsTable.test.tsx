import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, fireEvent } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { RunsTable, type RunsTableRow } from "./RunsTable"

afterEach(cleanup)

function makeRow(overrides: Partial<RunsTableRow> & { key: string }): RunsTableRow {
  return {
    label: overrides.key,
    command: "start",
    args: ["IPG-1"],
    kind: "start",
    target: "start:IPG-1",
    state: "succeeded",
    exitCode: 0,
    started: 1730000000,
    ...overrides,
  }
}

describe("RunsTable parent-child tree", () => {
  const parent = makeRow({
    key: "db-10",
    label: "db-10",
    command: "flow",
    args: ["run", "ship", "IPG-1"],
    kind: "flow",
    target: "flow:ship:IPG-1",
    state: "running",
    exitCode: null,
  })
  const child0 = makeRow({
    key: "db-11",
    label: "db-11",
    command: "start",
    args: ["IPG-1"],
    kind: "start",
    target: "start:IPG-1",
    state: "succeeded",
    exitCode: 0,
    parentRunId: "db-10",
    stepIndex: 0,
  })
  const child1 = makeRow({
    key: "db-12",
    label: "db-12",
    command: "review",
    args: ["IPG-1"],
    kind: "review",
    target: "review:IPG-1",
    state: "running",
    exitCode: null,
    parentRunId: "db-10",
    stepIndex: 1,
  })

  it("nests child runs under their parent and toggles collapse", () => {
    render(
      <MemoryRouter>
        <RunsTable rows={[parent, child1, child0]} />
      </MemoryRouter>,
    )
    expect(screen.getAllByText(/Flow: ship \(step 2\/2 running\)/).length).toBeGreaterThan(0)
    // Running parent starts expanded; children visible
    expect(screen.getAllByText("db-11").length).toBeGreaterThan(0)
    expect(screen.getAllByText("db-12").length).toBeGreaterThan(0)

    const toggle = screen.getAllByRole("button", { name: /Toggle child runs for db-10/ })[0]!
    fireEvent.click(toggle)
    expect(screen.queryByText("db-11")).toBeNull()
  })

  it("renders flat rows when flat=true", () => {
    render(
      <MemoryRouter>
        <RunsTable rows={[parent, child0, child1]} flat />
      </MemoryRouter>,
    )
    expect(screen.queryByRole("button", { name: /Toggle child runs/ })).toBeNull()
    expect(screen.getAllByText("db-11").length).toBeGreaterThan(0)
  })
})
