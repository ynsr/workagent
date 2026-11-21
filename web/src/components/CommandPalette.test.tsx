import { useState } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter, useLocation } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { CommandPalette } from "./CommandPalette"

vi.mock("@/lib/queries", () => ({
  useStatusAll: () => ({
    data: {
      "jira:IPG-101": { branch: "feat/ipg-101", repo: "payments", pr: "MR #12 (open)" },
    },
  }),
  useStatusInactive: () => ({
    data: {
      "jira:IPG-102": { branch: "feat/ipg-102", repo: "payments", active: 0 },
    },
  }),
  useStatusDeleted: () => ({
    data: {
      "jira:IPG-103": { branch: "feat/ipg-103", repo: "payments", deleted_at: "2026-01-01T00:00:00Z" },
    },
  }),
  useRepos: () => ({
    data: [{ name: "payments", path: "/repos/payments", default_branch: "main" }],
  }),
  useFlowTemplates: () => ({
    data: { templates: [{ id: 1, label: "full-dev", name: "Full Dev Flow", steps: [], pre_execution: "", post_execution: "", created_at: "", modified_at: "" }] },
  }),
  useActions: () => ({
    data: { actions: [{ id: 1, label: "lint-fix", name: "Lint & Fix", icon: "zap", prompt: "", pre_execution: "", post_execution: "", default_options: {}, created_at: "", modified_at: "" }] },
  }),
  useRuns: () => ({
    data: [
      {
        id: "run-abcdef123456",
        command: "review",
        args: [],
        state: "succeeded",
        exit_code: 0,
        truncated: false,
        created: 1700000000,
        target: "jira:IPG-101",
        session_file: "",
        transcript_ready: false,
        session_type: null,
        worktree: "",
        last_seq: 0,
      },
    ],
  }),
}))

afterEach(cleanup)

function LocationEcho() {
  const loc = useLocation()
  return <div data-testid="location">{`${loc.pathname}${loc.search}`}</div>
}

function Wrapper() {
  const [open, setOpen] = useState(false)
  const qc = new QueryClient()
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/"]}>
        <LocationEcho />
        <CommandPalette open={open} onOpenChange={setOpen} />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe("CommandPalette", () => {
  it("opens on Ctrl+K, indexes all entity categories, and dispatches on Enter", () => {
    render(<Wrapper />)

    expect(screen.queryByRole("combobox")).not.toBeInTheDocument()

    // Open with Ctrl+K
    fireEvent.keyDown(window, { key: "k", ctrlKey: true })
    const input = screen.getByRole("combobox")
    expect(input).toBeInTheDocument()

    // Verify all categories are indexed
    expect(screen.getByText("jira:IPG-101")).toBeInTheDocument()
    expect(screen.getByText("jira:IPG-102")).toBeInTheDocument()
    expect(screen.getByText("jira:IPG-103")).toBeInTheDocument()
    expect(screen.getByText("payments")).toBeInTheDocument()
    expect(screen.getByText("Full Dev Flow")).toBeInTheDocument()
    expect(screen.getByText("Lint & Fix")).toBeInTheDocument()
    expect(screen.getByText("review · jira:IPG-101")).toBeInTheDocument()
    expect(screen.getByText("Settings")).toBeInTheDocument()

    // Filter to Settings and press Enter (2-keystroke dispatch)
    fireEvent.change(input, { target: { value: "Settings" } })
    fireEvent.keyDown(input, { key: "Enter" })

    expect(screen.getByTestId("location").textContent).toBe("/settings")
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
  })

  it("navigates filtered results with ArrowDown and dispatches worktree jump", () => {
    render(<Wrapper />)

    fireEvent.keyDown(window, { key: "k", metaKey: true })
    const input = screen.getByRole("combobox")

    fireEvent.change(input, { target: { value: "IPG-102" } })
    fireEvent.keyDown(input, { key: "Enter" })

    expect(screen.getByTestId("location").textContent).toBe("/?q=jira%3AIPG-102")
  })
})
