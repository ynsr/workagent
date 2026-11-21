import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
import { ConfirmProvider } from "@/lib/confirm"
import { Messages, filterMessages } from "./Messages"
import type { MessageRow } from "@/lib/api"
import type * as QueriesModule from "@/lib/queries"

vi.mock("@/lib/queries", async () => {
  const actual = await vi.importActual<typeof QueriesModule>("@/lib/queries")
  const settled = <T,>(data: T) => () => ({
    data,
    isPending: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  })
  return {
    ...actual,
    useMessages: settled({ messages: [] }),
    useActions: settled({ actions: [] }),
    useFlowTemplates: settled({ templates: [] }),
    useRepos: settled([]),
    useStatusAll: settled({}),
    useConfig: settled({ default_harness: "agy", supported_harnesses: ["agy"], harnesses: [] }),
    useCreateRun: () => ({ mutateAsync: vi.fn() }),
  }
})

afterEach(cleanup)

const ROWS: MessageRow[] = [
  { id: 1, message_id: "p1", thread_id: "t1", app_name: "mattermost",
    sender_username: "u1", content: "deploy broke", ts: "", repos: ["r1"], added_at: "t" },
  { id: 2, message_id: "p2", thread_id: "t1", app_name: "mattermost",
    sender_username: "u2", content: "all green", ts: "", repos: ["r2"], added_at: "t" },
]

describe("filterMessages", () => {
  it("filters by text + sender", () => {
    expect(filterMessages(ROWS, { q: "deploy", sender: "u1", app: "", repo: "", thread: "" })).toHaveLength(1)
    expect(filterMessages(ROWS, { q: "nothing", sender: "", app: "", repo: "", thread: "" })).toHaveLength(0)
  })
  it("filters by thread and repo", () => {
    expect(filterMessages(ROWS, { q: "", sender: "", app: "", repo: "r2", thread: "t1" })).toHaveLength(1)
    expect(filterMessages(ROWS, { q: "", sender: "", app: "", repo: "zzz", thread: "" })).toHaveLength(0)
  })
})

describe("Messages launch drawer", () => {
  it("opens the Run on Message drawer from the header button", async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <ConfirmProvider>
            <Messages />
          </ConfirmProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    )

    await user.click(screen.getByRole("button", { name: "Run on Message" }))
    expect(screen.getByRole("dialog", { name: "Run on Message" })).toBeInTheDocument()
  })
})
