import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { CommitsCell, ReviewsCell } from "@/components/StatusCells"
import { CiBadge } from "@/components/StateBadge"
import type { WorktreeMap } from "@/lib/api"

afterEach(cleanup)

describe("StatusCells accessible labels", () => {
  it("renders sr-only commit divergence text for CommitsCell", () => {
    const entry = { commits_detail: { behind: 2, ahead: 5 } } as WorktreeMap[string]
    render(<CommitsCell entry={entry} />)
    expect(screen.getByText("2 commits behind, 5 commits ahead of base branch")).toHaveClass("sr-only")
  })

  it("renders sr-only unknown divergence text when commits_detail is missing", () => {
    render(<CommitsCell entry={{} as WorktreeMap[string]} />)
    expect(screen.getByText("Commit divergence unknown")).toHaveClass("sr-only")
  })

  it("renders sr-only label for ReviewsCell", () => {
    const entry = { reviews_detail: { reviews: 2, resolved: 3, unresolved: 1 } } as WorktreeMap[string]
    render(<ReviewsCell entry={entry} />)
    expect(screen.getByText("2 completed review passes, 3 of 4 threads resolved")).toHaveClass("sr-only")
  })

  it("renders sr-only label when ReviewsCell has no reviews", () => {
    render(<ReviewsCell entry={{} as WorktreeMap[string]} />)
    expect(screen.getByText("No reviews")).toHaveClass("sr-only")
  })
})

describe("CiBadge accessible labels", () => {
  it("renders sr-only status label for unlinked and linked CI badges", () => {
    render(<CiBadge ci="success" />)
    expect(screen.getByText("CI: success")).toHaveClass("sr-only")
    cleanup()

    render(<CiBadge ci="failure" ciUrl="https://example.com/jobs/1" />)
    expect(screen.getByText("CI: failure")).toHaveClass("sr-only")
  })
})
