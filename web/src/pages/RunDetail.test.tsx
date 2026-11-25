import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ConfirmProvider } from "@/lib/confirm"
import type { RunDetail as RunDetailData } from "@/lib/api"
import { RunDetail } from "@/pages/RunDetail"

const { mockCreateRun, mockRunData } = vi.hoisted(() => ({
  mockCreateRun: vi.fn(),
  mockRunData: { current: null as RunDetailData | null },
}))

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api")
  return {
    ...actual,
    api: {
      ...actual.api,
      createRun: mockCreateRun,
    },
  }
})

vi.mock("@/lib/queries", async () => {
  const actual = await vi.importActual<typeof import("@/lib/queries")>("@/lib/queries")
  return {
    ...actual,
    useRun: () => ({
      data: mockRunData.current,
      isPending: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    }),
    useCancelRun: () => ({ isPending: false, mutateAsync: vi.fn() }),
  }
})

vi.mock("@/hooks/useRunEvents", () => ({
  mergeLines: (sseLines: unknown[], initialLines: unknown[] = []) =>
    sseLines.length > 0 ? sseLines : initialLines,
  useRunEvents: () => ({
    lines: [],
    state: null,
    exitCode: null,
    connected: false,
  }),
}))

function renderRunDetail() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={["/runs/run-183"]}>
      <QueryClientProvider client={client}>
        <ConfirmProvider>
          <Routes>
            <Route path="/runs/:runId" element={<RunDetail />} />
            <Route path="/runs/:runId/*" element={<RunDetail />} />
          </Routes>
        </ConfirmProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe("RunDetail Re-run (issue #183)", () => {
  afterEach(() => {
    cleanup()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    mockCreateRun.mockResolvedValue({ run_id: "run-next" })
  })

  it("adds --fresh when re-running a flow run that failed with --steps differs error", async () => {
    mockRunData.current = {
      id: "run-183",
      command: "flow",
      args: ["run", "review_fix", "ao/wave2-iban/root", "--steps", "[]", "--yes"],
      state: "needs_input",
      exit_code: 2,
      truncated: false,
      created: 1_760_000_000,
      target: "flow:review_fix:ao/wave2-iban/root",
      session_file: "",
      transcript_ready: false,
      session_type: "flow",
      worktree: "/tmp/wt",
      last_seq: 1,
      lines: [
        {
          seq: 1,
          text: "error: --steps differs from the resumable run 155 (current step 0) — re-run with --fresh for a new run on the new chain, or without --steps to resume",
        },
      ],
    }

    renderRunDetail()

    expect(
      screen.getByText(/--fresh will be added automatically/i),
    ).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /Re-run/i }))

    const confirmBtn = await screen.findByRole("button", { name: /^Re-run$/i })
    fireEvent.click(confirmBtn)

    await waitFor(() => {
      expect(mockCreateRun).toHaveBeenCalledWith({
        command: "flow",
        args: ["run", "review_fix", "ao/wave2-iban/root", "--steps", "[]", "--fresh"],
        confirm: true,
      })
    })
  })

  it("does not add --fresh for other needs_input errors", async () => {
    mockRunData.current = {
      id: "run-184",
      command: "flow",
      args: ["run", "review_fix", "ao/wave2-iban/root", "--yes"],
      state: "needs_input",
      exit_code: 2,
      truncated: false,
      created: 1_760_000_000,
      target: "flow:review_fix:ao/wave2-iban/root",
      session_file: "",
      transcript_ready: false,
      session_type: "flow",
      worktree: "/tmp/wt",
      last_seq: 1,
      lines: [{ seq: 1, text: "error: no linked state" }],
    }

    renderRunDetail()

    expect(
      screen.queryByText(/--fresh will be added automatically/i),
    ).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /Re-run/i }))

    const confirmBtn = await screen.findByRole("button", { name: /^Re-run$/i })
    fireEvent.click(confirmBtn)

    await waitFor(() => {
      expect(mockCreateRun).toHaveBeenCalledWith({
        command: "flow",
        args: ["run", "review_fix", "ao/wave2-iban/root"],
        confirm: true,
      })
    })
  })
})
