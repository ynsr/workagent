import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import {
  WorkspaceDiffView,
  formatAnnotationsPrompt,
  type LineAnnotation,
} from "./WorkspaceDiffView"
import { api } from "@/lib/api"

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("formatAnnotationsPrompt", () => {
  it("formats staged annotations with file, line, comment, and snippet", () => {
    const items: LineAnnotation[] = [
      {
        id: "1",
        path: "src/app.py",
        line: 12,
        side: "new",
        code: "return x + 1",
        comment: "Handle None case",
      },
    ]
    const out = formatAnnotationsPrompt(items)
    expect(out).toContain("src/app.py")
    expect(out).toContain("line 12")
    expect(out).toContain("Handle None case")
    expect(out).toContain("return x + 1")
  })
})

describe("WorkspaceDiffView", () => {
  it("renders file tree, toggles Split view, and dispatches inline line annotations", async () => {
    vi.spyOn(api, "worktreeFiles").mockResolvedValue({
      key: "jira:T-1",
      mode: "base",
      base_ref: "abc123",
      total_additions: 2,
      total_deletions: 1,
      files: [
        { path: "src/app.py", status: "M", additions: 2, deletions: 1, binary: false },
      ],
    })
    vi.spyOn(api, "worktreeDiff").mockResolvedValue({
      key: "jira:T-1",
      path: "src/app.py",
      mode: "base",
      base_ref: "abc123",
      binary: false,
      truncated: false,
      additions: 2,
      deletions: 1,
      hunks: [
        {
          header: "@@ -1,1 +1,2 @@",
          old_start: 1,
          old_count: 1,
          new_start: 1,
          new_count: 2,
          lines: [
            { type: "del", old_line: 1, new_line: null, content: "x = 1" },
            { type: "add", old_line: null, new_line: 1, content: "x = 2" },
            { type: "add", old_line: null, new_line: 2, content: "y = 3" },
          ],
        },
      ],
    })
    const onDispatch = vi.fn()
    const user = userEvent.setup()

    wrap(<WorkspaceDiffView worktreeRef="jira:T-1" onDispatchAnnotations={onDispatch} />)

    expect(await screen.findByText("src/app.py")).toBeInTheDocument()
    expect(await screen.findByText("@@ -1,1 +1,2 @@")).toBeInTheDocument()

    // Toggle to Split view and back
    await user.click(screen.getByRole("button", { name: "Split" }))
    await user.click(screen.getByRole("button", { name: "Unified" }))

    // Add an inline comment on line 2
    await user.click(screen.getByRole("button", { name: "Comment on src/app.py line 2" }))
    await user.type(screen.getByLabelText("Inline line comment"), "Rename y to count")
    await user.click(screen.getByRole("button", { name: "Add comment" }))

    expect(screen.getByTestId("annotation-batch-bar")).toHaveTextContent("1 inline comment staged")
    await user.click(screen.getByRole("button", { name: "Send to Agent" }))

    expect(onDispatch).toHaveBeenCalledTimes(1)
    const prompt = String(onDispatch.mock.calls[0]?.[0] ?? "")
    expect(prompt).toContain("Rename y to count")
    expect(prompt).toContain("src/app.py")
  })
})
