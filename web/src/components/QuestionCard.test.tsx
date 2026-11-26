import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen, fireEvent, waitFor, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { QuestionCard } from "@/components/QuestionCard"
import { api } from "@/lib/api"

vi.mock("@/lib/api", async (importOriginal) => {
  const mod = await importOriginal<object>()
  return { ...mod, api: { ...(mod as { api: object }).api, submitRunInput: vi.fn(async () => ({ id: "r1", ok: true })) } }
})

function renderCard(options: string[] = ["yes", "no"]) {
  const qc = new QueryClient()
  const view = render(
    <QueryClientProvider client={qc}>
      <QuestionCard
        runId="r1"
        question={{ qid: "q1", text: "proceed?", options, multi: false }}
      />
    </QueryClientProvider>,
  )
  return { ...view, card: within(view.getByTestId("question-card")) }
}

afterEach(() => {
  vi.clearAllMocks()
  cleanup()
})

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

  it("marks Send aria-disabled with no answer, blocks click, and shows Radix tooltip", async () => {
    const { card } = renderCard()
    const sendBtn = card.getByRole("button", { name: /send/i })
    expect(sendBtn).toHaveAttribute("aria-disabled", "true")
    expect(sendBtn).not.toHaveAttribute("title")

    fireEvent.click(sendBtn)
    expect(api.submitRunInput).not.toHaveBeenCalled()

    fireEvent.focus(sendBtn)
    const tooltip = await screen.findByRole("tooltip")
    expect(tooltip).toHaveTextContent("Pick or type an answer first")
    expect(sendBtn).toHaveAttribute("aria-describedby", tooltip.id)
  })

  it("preserves focus indication on the free-text row and omits role=group when options are empty", () => {
    const { card } = renderCard([])
    expect(card.queryByRole("group", { name: "Answer options" })).not.toBeInTheDocument()
    const input = card.getByLabelText("Answer")
    expect(input.parentElement?.className).toMatch(/focus-within:ring-\[3px\]/)
  })
})
