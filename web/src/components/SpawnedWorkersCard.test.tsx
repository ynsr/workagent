import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it } from "vitest"
import { SpawnedWorkersCard } from "./SpawnedWorkersCard"
import type { SpawnedRunSummary } from "@/lib/api"

const SAMPLE_WORKERS: SpawnedRunSummary[] = [
  {
    id: "db-101",
    command: "start",
    state: "running",
    exit_code: null,
    created: 1700000000,
    target: "start:github:o/r#201",
    harness_name: "omp",
    worktree: "/tmp/wt-1",
    worktree_ref: "github:o/r#201",
    branch: "feat/201-auth",
  },
  {
    id: "db-102",
    command: "start",
    state: "succeeded",
    exit_code: 0,
    created: 1700000010,
    target: "start:github:o/r#202",
    harness_name: "agy",
    worktree: "/tmp/wt-2",
    worktree_ref: "github:o/r#202",
    branch: "feat/202-billing",
  },
]

describe("SpawnedWorkersCard", () => {
  it("renders nothing when workers list is empty", () => {
    const { container } = render(
      <MemoryRouter>
        <SpawnedWorkersCard workers={[]} />
      </MemoryRouter>,
    )
    expect(container.firstChild).toBeNull()
  })

  it("renders worker count, live spinner, harness badges, branch slugs, and workspace links", () => {
    render(
      <MemoryRouter>
        <SpawnedWorkersCard workers={SAMPLE_WORKERS} />
      </MemoryRouter>,
    )

    expect(screen.getByText(/Started 2 workers — Each in its own worktree/i)).toBeInTheDocument()
    expect(screen.getByTestId("worker-spinner")).toBeInTheDocument()
    expect(screen.getByText("OMP")).toBeInTheDocument()
    expect(screen.getByText("AGY")).toBeInTheDocument()
    expect(screen.getByText("feat/201-auth")).toBeInTheDocument()
    expect(screen.getByText("feat/202-billing")).toBeInTheDocument()

    const links = screen.getAllByRole("link", { name: "Open workspace" })
    expect(links).toHaveLength(2)
    expect(links[0]).toHaveAttribute("href", "/worktrees/github%3Ao%2Fr%23201")
    expect(links[1]).toHaveAttribute("href", "/worktrees/github%3Ao%2Fr%23202")
  })
})
