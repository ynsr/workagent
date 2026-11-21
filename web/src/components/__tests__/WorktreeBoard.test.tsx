import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { WorktreeBoard } from "@/components/WorktreeBoard"
import type { WorktreeMap } from "@/lib/api"

afterEach(cleanup)

const noopActions = {
  onOpenWorktree: vi.fn(),
  onOpenRun: vi.fn(),
  onSync: vi.fn(),
  onReview: vi.fn(),
  onFixComments: vi.fn(),
  onCleanup: vi.fn(),
}

const sampleWorktrees: WorktreeMap = {
  "jira:IPG-1": {
    issue: "IPG-1",
    branch: "feat/IPG-1",
    harness: "agy 101",
    kanban_column: "working",
    display_status: "Working · agy",
    attention_reason: null,
  },
  "jira:IPG-2": {
    issue: "IPG-2",
    branch: "feat/IPG-2",
    ci: "failure",
    pr_detail: { number: 22, state: "open", title: "Fix CI", author: "alice", url: "https://x/22", tool: "gh" },
    reviews_detail: { reviews: 1, unresolved: 2, resolved: 0 },
    kanban_column: "needs_you",
    display_status: "CI failed",
    attention_reason: "ci_failed",
  },
  "jira:IPG-3": {
    issue: "IPG-3",
    branch: "feat/IPG-3",
    kanban_column: "needs_you",
    display_status: "Waiting for your answer",
    attention_reason: "pending_question",
  },
  "jira:IPG-4": {
    issue: "IPG-4",
    branch: "feat/IPG-4",
    ci: "running",
    pr_detail: { number: 44, state: "open", title: "In review PR", author: "bob", url: "https://x/44", tool: "gh" },
    kanban_column: "in_review",
    display_status: "CI running",
    attention_reason: null,
  },
  "jira:IPG-5": {
    issue: "IPG-5",
    branch: "feat/IPG-5",
    ci: "success",
    commits_detail: { behind: 0, ahead: 2 },
    pr_detail: { number: 55, state: "open", title: "Clean PR", author: "bob", url: "https://x/55", tool: "gh" },
    prodtest: "pending",
    kanban_column: "ready_to_merge",
    display_status: "Ready to merge",
    attention_reason: null,
  },
}

describe("WorktreeBoard", () => {
  it("renders 4 semantic lanes with attention-first sorting and sr-only labels", () => {
    render(<WorktreeBoard worktrees={sampleWorktrees} actions={noopActions} />)

    const needsLane = screen.getByTestId("board-lane-needs_you")
    const workingLane = screen.getByTestId("board-lane-working")
    const reviewLane = screen.getByTestId("board-lane-in_review")
    const readyLane = screen.getByTestId("board-lane-ready_to_merge")

    // pending_question sorts before ci_failed in Needs you
    const needsCards = within(needsLane).getAllByRole("article")
    expect(needsCards).toHaveLength(2)
    expect(needsCards[0]).toHaveAttribute("data-testid", "board-card-jira:IPG-3")
    expect(needsCards[1]).toHaveAttribute("data-testid", "board-card-jira:IPG-2")

    // Coral attention border on Needs you
    expect(needsCards[0]?.className).toContain("#f06445")

    // Harness badge in Working
    expect(within(workingLane).getByText("AGY")).toBeInTheDocument()
    expect(within(workingLane).getByText("Working · agy")).toBeInTheDocument()

    // In review & Ready to merge
    expect(within(reviewLane).getByText("CI running")).toBeInTheDocument()
    const readyCard = within(readyLane).getByTestId("board-card-jira:IPG-5")
    expect(within(readyCard).getByText("Ready to merge")).toBeInTheDocument()
    expect(within(readyCard).getByText("PT: pending")).toBeInTheDocument()
    expect(within(readyCard).getByText(/Behind and ahead commits:/)).toHaveClass("sr-only")
  })

  it("invokes quick hover actions", async () => {
    const user = userEvent.setup()
    const onSync = vi.fn()
    render(
      <WorktreeBoard
        worktrees={{ "jira:IPG-1": sampleWorktrees["jira:IPG-1"]! }}
        actions={{ ...noopActions, onSync }}
      />,
    )
    await user.click(screen.getByRole("button", { name: "Sync jira:IPG-1" }))
    expect(onSync).toHaveBeenCalledWith("jira:IPG-1")
  })
})
