import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { Schedules } from "./Schedules"
import { api } from "@/lib/api"

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api")
  return {
    ...actual,
    api: {
      ...actual.api,
      schedules: vi.fn(),
      previewSchedule: vi.fn(),
      createSchedule: vi.fn(),
      updateSchedule: vi.fn(),
      deleteSchedule: vi.fn(),
      runScheduleNow: vi.fn(),
    },
  }
})

function renderSchedules() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Schedules />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("Schedules page (T13.3)", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.schedules).mockResolvedValue({
      schedules: [
        {
          id: 1,
          name: "Daily PR review",
          cron_expr: "0 9 * * 1-5",
          timezone: "UTC",
          target_kind: "command",
          target_label: "review --all --yes",
          args_json: "{}",
          args: {},
          enabled: true,
          last_run_at: "2026-06-01T09:00:00+00:00",
          next_run_at: "2026-06-02T09:00:00+00:00",
          last_run_id: 42,
          lease_until: "",
          created_at: "2026-05-01T00:00:00+00:00",
          modified_at: "2026-06-01T09:00:00+00:00",
        },
      ],
      starters: [
        {
          id: "daily-unreviewed-pr-review",
          name: "Daily unreviewed PR review",
          description: "Review all open unreviewed PRs/MRs every weekday morning.",
          cron_expr: "0 9 * * 1-5",
          timezone: "UTC",
          target_kind: "command",
          target_label: "review --all --yes",
          args_json: "{}",
        },
        {
          id: "daily-merged-worktree-cleanup",
          name: "Daily merged worktree cleanup",
          description: "Clean up merged worktrees every evening.",
          cron_expr: "0 20 * * *",
          timezone: "UTC",
          target_kind: "command",
          target_label: "cleanup --merged --yes",
          args_json: "{}",
        },
        {
          id: "post-release-prodtest-sweep",
          name: "Post-release prodtest sweep",
          description: "Run pending production test plans every 6 hours.",
          cron_expr: "0 */6 * * *",
          timezone: "UTC",
          target_kind: "command",
          target_label: "prodtest run --all --yes",
          args_json: "{}",
        },
      ],
    })
    vi.mocked(api.previewSchedule).mockResolvedValue({
      cron_expr: "0 20 * * *",
      timezone: "UTC",
      next_runs: ["2026-06-01T20:00:00+00:00", "2026-06-02T20:00:00+00:00"],
    })
  })

  it("renders starter templates, configured schedules, and run history link", async () => {
    renderSchedules()

    expect(await screen.findByText("Daily PR review")).toBeInTheDocument()
    expect(screen.getByText("Daily unreviewed PR review")).toBeInTheDocument()
    expect(screen.getByText("Daily merged worktree cleanup")).toBeInTheDocument()
    expect(screen.getByText("Post-release prodtest sweep")).toBeInTheDocument()

    const runLink = screen.getByRole("link", { name: "Run #42" })
    expect(runLink).toHaveAttribute("href", "/runs/db-42")
  })

  it("pre-fills form from starter template and shows next-run preview", async () => {
    const user = userEvent.setup()
    renderSchedules()

    await screen.findByText("Daily merged worktree cleanup")
    const useButtons = screen.getAllByRole("button", { name: /Use template/i })
    await user.click(useButtons[1]!)

    expect(screen.getByLabelText("Name")).toHaveValue("Daily merged worktree cleanup")
    expect(screen.getByLabelText("Cron / RRULE Expression")).toHaveValue("0 20 * * *")

    await waitFor(() => {
      expect(screen.getByText("2026-06-01T20:00:00+00:00")).toBeInTheDocument()
    })
  })
})
