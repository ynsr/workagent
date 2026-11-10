import { describe, expect, it } from "vitest"
import { filterMessages } from "./Messages"
import type { MessageRow } from "@/lib/api"

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
