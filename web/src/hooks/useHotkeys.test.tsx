import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, fireEvent } from "@testing-library/react"
import { useHotkeys, useListHotkeys, isEditableTarget } from "./useHotkeys"

afterEach(cleanup)

function HotkeyHarness({
  onModK,
  onModF,
  onSlash,
  onJ,
  onK,
  onEnter,
  onSync,
  onReview,
  onCleanup,
}: {
  onModK?: () => void
  onModF?: () => void
  onSlash?: () => void
  onJ?: () => void
  onK?: () => void
  onEnter?: () => void
  onSync?: () => void
  onReview?: () => void
  onCleanup?: () => void
}) {
  useHotkeys([
    { key: "Mod+K", allowInInputs: true, handler: () => onModK?.() },
    { key: "Mod+F", allowInInputs: true, handler: () => onModF?.() },
    { key: "/", handler: () => onSlash?.() },
    { key: "j", handler: () => onJ?.() },
    { key: "k", handler: () => onK?.() },
    { key: "Enter", handler: () => onEnter?.() },
    { key: "s", handler: () => onSync?.() },
    { key: "r", handler: () => onReview?.() },
    { key: "c", handler: () => onCleanup?.() },
  ])
  return (
    <div>
      <input data-testid="text-input" />
      <textarea data-testid="text-area" />
      <div data-testid="editable-div" contentEditable suppressContentEditableWarning />
      <button data-testid="plain-btn" type="button">
        OK
      </button>
    </div>
  )
}

describe("useHotkeys", () => {
  it("fires Mod+K (Ctrl+K and Meta+K), Mod+F, /, j, k, Enter, s, r, c outside inputs", () => {
    const onModK = vi.fn()
    const onModF = vi.fn()
    const onSlash = vi.fn()
    const onJ = vi.fn()
    const onK = vi.fn()
    const onEnter = vi.fn()
    const onSync = vi.fn()
    const onReview = vi.fn()
    const onCleanup = vi.fn()

    render(
      <HotkeyHarness
        onModK={onModK}
        onModF={onModF}
        onSlash={onSlash}
        onJ={onJ}
        onK={onK}
        onEnter={onEnter}
        onSync={onSync}
        onReview={onReview}
        onCleanup={onCleanup}
      />,
    )

    fireEvent.keyDown(window, { key: "k", ctrlKey: true })
    fireEvent.keyDown(window, { key: "k", metaKey: true })
    expect(onModK).toHaveBeenCalledTimes(2)

    fireEvent.keyDown(window, { key: "f", ctrlKey: true })
    expect(onModF).toHaveBeenCalledTimes(1)

    fireEvent.keyDown(window, { key: "/" })
    fireEvent.keyDown(window, { key: "j" })
    fireEvent.keyDown(window, { key: "k" })
    fireEvent.keyDown(window, { key: "Enter" })
    fireEvent.keyDown(window, { key: "s" })
    fireEvent.keyDown(window, { key: "r" })
    fireEvent.keyDown(window, { key: "c" })

    expect(onSlash).toHaveBeenCalledTimes(1)
    expect(onJ).toHaveBeenCalledTimes(1)
    expect(onK).toHaveBeenCalledTimes(1)
    expect(onEnter).toHaveBeenCalledTimes(1)
    expect(onSync).toHaveBeenCalledTimes(1)
    expect(onReview).toHaveBeenCalledTimes(1)
    expect(onCleanup).toHaveBeenCalledTimes(1)
  })

  it("suppresses non-Mod shortcuts in input, textarea, and contenteditable, while allowing Mod+K", () => {
    const onModK = vi.fn()
    const onJ = vi.fn()
    const onSlash = vi.fn()
    const onSync = vi.fn()

    const { getByTestId } = render(
      <HotkeyHarness onModK={onModK} onJ={onJ} onSlash={onSlash} onSync={onSync} />,
    )

    for (const id of ["text-input", "text-area", "editable-div"]) {
      const el = getByTestId(id)
      expect(isEditableTarget(el)).toBe(true)
      fireEvent.keyDown(el, { key: "j", bubbles: true })
      fireEvent.keyDown(el, { key: "/", bubbles: true })
      fireEvent.keyDown(el, { key: "s", bubbles: true })
    }

    expect(onJ).not.toHaveBeenCalled()
    expect(onSlash).not.toHaveBeenCalled()
    expect(onSync).not.toHaveBeenCalled()

    // Mod+K with allowInInputs: true still fires inside an input
    fireEvent.keyDown(getByTestId("text-input"), { key: "k", ctrlKey: true, bubbles: true })
    expect(onModK).toHaveBeenCalledTimes(1)
  })
})

function ListHarness({
  items,
  onOpen,
  onSync,
}: {
  items: string[]
  onOpen: (item: string) => void
  onSync?: (item: string) => void
}) {
  const { activeIndex } = useListHotkeys({
    items,
    onOpen: (item) => onOpen(item),
    onSync: onSync ? (item) => onSync(item) : undefined,
  })
  return <div data-testid="active-index">{activeIndex}</div>
}

describe("useListHotkeys", () => {
  it("moves activeIndex with j/k and dispatches Enter and s on the active item", () => {
    const onOpen = vi.fn()
    const onSync = vi.fn()
    const { getByTestId } = render(
      <ListHarness items={["alpha", "beta", "gamma"]} onOpen={onOpen} onSync={onSync} />,
    )

    expect(getByTestId("active-index").textContent).toBe("-1")

    fireEvent.keyDown(window, { key: "j" })
    expect(getByTestId("active-index").textContent).toBe("0")

    fireEvent.keyDown(window, { key: "j" })
    expect(getByTestId("active-index").textContent).toBe("1")

    fireEvent.keyDown(window, { key: "Enter" })
    expect(onOpen).toHaveBeenCalledWith("beta")

    fireEvent.keyDown(window, { key: "s" })
    expect(onSync).toHaveBeenCalledWith("beta")

    fireEvent.keyDown(window, { key: "k" })
    expect(getByTestId("active-index").textContent).toBe("0")
  })
})
