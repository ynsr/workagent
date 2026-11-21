import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ActionFormCard } from "@/components/ActionForm"
import type { ActionRow } from "@/lib/api"

const { mutateAsync, mockConfig } = vi.hoisted(() => ({
  mutateAsync: vi.fn<(...args: [{ command: string; args: string[] }]) => Promise<{ run_id: string }>>(
    () => Promise.resolve({ run_id: "run-1" }),
  ),
  mockConfig: {
    default_harness: "agy",
    supported_harnesses: ["agy", "omp"],
    harnesses: [
      { name: "agy", label: "Antigravity (agy-guard)", binary: "agy-guard", available: true },
      { name: "omp", label: "OMP (omp)", binary: "omp", available: true },
    ],
  },
}))

vi.mock("@/lib/queries", () => ({
  queryKeys: { harnessOptions: (name: string) => ["harnesses", "options", name] },
  useConfig: () => ({ data: mockConfig }),
  useCreateRun: () => ({ mutateAsync }),
}))

function renderForm(editing: ActionRow | null = null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <ActionFormCard editing={editing} onDone={vi.fn()} onCancel={vi.fn()} />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  mutateAsync.mockClear()
})

afterEach(cleanup)

describe("ActionFormCard harness", () => {
  it("persists harness in default_options and forwards --harness to Suggest icon", async () => {
    const user = userEvent.setup()
    renderForm(null)

    await user.type(screen.getByLabelText("Name"), "Audit")
    await user.type(screen.getByLabelText("Prompt"), "audit repo")
    await user.click(screen.getByRole("radio", { name: "omp" }))

    await user.click(screen.getByRole("button", { name: "Suggest icon" }))
    const suggestCall = mutateAsync.mock.calls[0]?.[0]
    expect(suggestCall?.command).toBe("action")
    expect(suggestCall?.args).toEqual([
      "suggest-icon",
      "--name", "Audit",
      "--label", "audit",
      "--prompt", "audit repo",
      "--harness", "omp",
      "--json",
    ])

    mutateAsync.mockClear()
    await user.click(screen.getByRole("button", { name: "Add action" }))
    expect(mutateAsync).toHaveBeenCalledTimes(1)
    const call = mutateAsync.mock.calls[0]?.[0]
    expect(call?.command).toBe("action")
    const args = call?.args ?? []
    const idx = args.indexOf("--default-options")
    expect(JSON.parse(args[idx + 1] ?? "{}")).toEqual({ run_mode: "preview", harness: "omp" })
  })

  it("hydrates default_options.harness when editing an existing action", () => {
    renderForm({
      id: 1,
      label: "audit",
      name: "Audit",
      icon: "Zap",
      prompt: "audit repo",
      pre_execution: "",
      post_execution: "",
      default_options: { run_mode: "headless", harness: "omp" },
      created_at: "",
      modified_at: "",
    })
    expect(screen.getByRole("radio", { name: "omp" })).toHaveAttribute("aria-checked", "true")
  })
})
