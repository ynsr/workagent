import { describe, expect, it } from "vitest"
import { filterProdtests } from "./Prodtests"
import type { ProdtestRow } from "@/lib/api"

const ROWS: ProdtestRow[] = [
  { issue_key: "IPG-932", status: "passed", plan_path: "plans/p932.md", worktree_ref: "wt-932", last_run_id: "r1" },
  { issue_key: "IPG-933", status: "pending", plan_path: "plans/p933.md", worktree_ref: "", last_run_id: "" },
]

describe("filterProdtests", () => {
  it("matches issue/plan/worktree/run substring", () => {
    expect(filterProdtests(ROWS, { q: "932", status: "" })).toHaveLength(1)
    expect(filterProdtests(ROWS, { q: "plans/p933", status: "" })[0]?.issue_key).toBe("IPG-933")
    expect(filterProdtests(ROWS, { q: "wt-932", status: "" })).toHaveLength(1)
    expect(filterProdtests(ROWS, { q: "nothing", status: "" })).toHaveLength(0)
  })
  it("filters by exact status", () => {
    expect(filterProdtests(ROWS, { q: "", status: "passed" })).toHaveLength(1)
    expect(filterProdtests(ROWS, { q: "IPG", status: "pending" })[0]?.issue_key).toBe("IPG-933")
    expect(filterProdtests(ROWS, { q: "", status: "failed" })).toHaveLength(0)
  })
})
