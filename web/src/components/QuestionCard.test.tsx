import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen, fireEvent, waitFor, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { QuestionCard } from "@/components/QuestionCard"
import { api } from "@/lib/api"

vi.mock("@/lib/api", async (importOriginal) => {
  const mod = await importOriginal<object>()
  return { ...mod, api: { ...(mod as { api: object }).api, submitRunInput: vi.fn(async () => ({ id: "r1", ok: true })) } }
})

function renderCard() {
  const qc = new QueryClient()
  const view = render(
    <QueryClientProvider client={qc}>
      <QuestionCard
        runId="r1"
        question={{ qid: "q1", text: "proceed?", options: ["yes", "no"], multi: false }}
      />
    </QueryClientProvider>,
  )
  return { ...view, card: within(view.getByTestId("question-card")) }
}

afterEach(() => cleanup())

describe("QuestionCard", () => {
  it("sends the picked option", async () => {
    renderCard()
    fireEvent.click(screen.getByRole("button", { name: "yes" }))
    fireEvent.click(screen.getByRole("button", { name: /send/i }))
    await waitFor(() => expect(api.submitRunInput).toHaveBeenCalledWith("r1", { text: "yes", qid: "q1" }))
  })

  it("sends free text", async () => {
    const { card } = renderCard()
    fireEvent.change(card.getByLabelText("Answer"), { target: { value: "custom answer" } })
    fireEvent.click(card.getByRole("button", { name: /send/i }))
    await waitFor(() => expect(api.submitRunInput).toHaveBeenCalledWith("r1", { text: "custom answer", qid: "q1" }))
  })

  it("disables Send with no answer", () => {
    const { card } = renderCard()
    expect(card.getByRole("button", { name: /send/i })).toBeDisabled()
  })
})
