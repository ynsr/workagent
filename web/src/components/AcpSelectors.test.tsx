import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
import { HarnessSelect } from "@/components/HarnessSelect"
import { EffortSelect, ModelSelect } from "@/components/ModelEffortSelect"
import { RunModeSelect, useStartForm } from "@/components/StartForm"
import { act, renderHook } from "@testing-library/react"
import { loadLaunchPrefs, loadStartPrefsByRepo, lookupStartPrefsByRepo, saveLaunchPrefs, saveStartPrefsForRepo } from "@/lib/launchPrefs"
import { buildImmediateMessageActionArgs } from "@/lib/messageRunConfig"

const mockConfig = vi.hoisted(() => ({
  data: {
    default_harness: "agy",
    supported_harnesses: ["agy", "omp"],
    harnesses: [
      { name: "agy", label: "Antigravity (agy-guard)", binary: "agy-guard", available: true },
      { name: "omp", label: "OMP (omp)", binary: "omp", available: true },
    ],
  },
}))

const optionsData = vi.hoisted(() => ({
  data: {
    supported: true,
    models: [{ id: "m1", name: "Model One" }],
    efforts: [{ id: "high", name: "High" }],
    current_model: "m1",
    current_effort: "high",
    warning: "",
  },
}))

vi.mock("@/lib/queries", () => ({
  useConfig: () => ({ data: mockConfig.data }),
}))

vi.mock("@/hooks/useHarnessOptions", () => ({
  useHarnessOptions: () => ({ data: optionsData.data, isPending: false, isError: false }),
}))

function renderWithClient(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>{ui}</QueryClientProvider>
    </MemoryRouter>,
  )
}

afterEach(cleanup)

describe("RunModeSelect (issue #112)", () => {
  it("offers all 4 modes including acp", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    renderWithClient(<RunModeSelect idPrefix="t" value="preview" onChange={onChange} />)
    expect(screen.getByRole("radio", { name: "ACP" })).toBeInTheDocument()
    await user.click(screen.getByRole("radio", { name: "ACP" }))
    expect(onChange).toHaveBeenCalledWith("acp")
  })
})

describe("HarnessSelect (issue #112)", () => {
  it("lists only omp and agy plus default", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    renderWithClient(<HarnessSelect idPrefix="t" value="" onChange={onChange} />)
    expect(screen.getByRole("radio", { name: "Default (agy)" })).toBeInTheDocument()
    expect(screen.getByRole("radio", { name: "omp" })).toBeInTheDocument()
    expect(screen.getByRole("radio", { name: "agy" })).toBeInTheDocument()
    expect(screen.queryByRole("radio", { name: /claude/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole("radio", { name: "omp" }))
    expect(onChange).toHaveBeenCalledWith("omp")
  })
})

describe("ModelSelect/EffortSelect (issue #112)", () => {
  it("renders discovered options with Default empty", async () => {
    const user = userEvent.setup()
    const onModel = vi.fn()
    const onEffort = vi.fn()
    renderWithClient(
      <>
        <ModelSelect idPrefix="t" harness="agy" value="" onChange={onModel} />
        <EffortSelect idPrefix="t" harness="agy" value="" onChange={onEffort} />
      </>,
    )
    const model = screen.getByLabelText<HTMLSelectElement>("Model")
    expect(model).not.toBeDisabled()
    expect(Array.from(model.options).map((o) => o.value)).toEqual(["", "m1"])
    await user.selectOptions(model, "m1")
    expect(onModel).toHaveBeenCalledWith("m1")

    const effort = screen.getByLabelText<HTMLSelectElement>("Effort")
    expect(Array.from(effort.options).map((o) => o.value)).toEqual(["", "high"])
    await user.selectOptions(effort, "high")
    expect(onEffort).toHaveBeenCalledWith("high")
  })
})

describe("UI drift fixes (#128)", () => {
  it("persists and loads model/effort in launch prefs", () => {
    const prefs = loadLaunchPrefs()
    saveLaunchPrefs({
      ...prefs,
      start: { ...prefs.start, model: "gemini-pro", effort: "high" },
    })
    const reloaded = loadLaunchPrefs()
    expect(reloaded.start.model).toBe("gemini-pro")
    expect(reloaded.start.effort).toBe("high")
  })

  it("sticks Start base/action/run-mode per repo (issue #114)", () => {
    saveStartPrefsForRepo("proj-a", { runMode: "acp", startAction: "plan", base: "develop" })
    saveStartPrefsForRepo("proj-b", { runMode: "headless", startAction: "implement", base: "main" })
    const all = loadStartPrefsByRepo()
    expect(all["proj-a"]).toEqual({ runMode: "acp", startAction: "plan", base: "develop" })
    expect(all["proj-b"]).toEqual({ runMode: "headless", startAction: "implement", base: "main" })
    // Corrupt entries are dropped, valid ones survive.
    localStorage.setItem("launch.startByRepo.v1", JSON.stringify({ ok: all["proj-a"], bad: { runMode: "nope" } }))
    expect(loadStartPrefsByRepo()).toEqual({ ok: { runMode: "acp", startAction: "plan", base: "develop" } })
  })
  it("looks up saved per-repo prefs case-insensitively (issue #114)", () => {
    localStorage.clear()
    saveStartPrefsForRepo("Proj-A", { runMode: "acp", startAction: "plan", base: "develop" })
    expect(lookupStartPrefsByRepo("proj-a")[1]).toEqual({ runMode: "acp", startAction: "plan", base: "develop" })
    // Saving under a different case reuses the stored key (no dup entries).
    saveStartPrefsForRepo("PROJ-a", { runMode: "headless", startAction: "implement", base: "main" })
    expect(Object.keys(loadStartPrefsByRepo())).toEqual(["Proj-A"])
    expect(lookupStartPrefsByRepo("proj-a")[1]?.base).toBe("main")
  })

  it("useStartForm reloads saved prefs on repo pick and prunes stale related (issue #114)", () => {
    localStorage.clear()
    saveStartPrefsForRepo("proj-a", { runMode: "acp", startAction: "plan", base: "develop" })
    const { result } = renderHook(() => useStartForm({ ref: "IPG-1", relatedRepos: ["proj-a", "other"] }))
    act(() => {
      result.current.update("repo", "proj-a")
    })
    expect(result.current.form.repo).toBe("proj-a")
    expect(result.current.form.runMode).toBe("acp")
    expect(result.current.form.startAction).toBe("plan")
    expect(result.current.form.base).toBe("develop")
    // Stale self-selection pruned in the same update.
    expect(result.current.form.relatedRepos).toEqual(["other"])
  })
  it("buildImmediateMessageActionArgs forwards runMode, harness, model, and effort", () => {
    const args = buildImmediateMessageActionArgs("triage", "m-1", {
      runMode: "acp",
      harness: "omp",
      model: "m1",
      effort: "high",
    })
    expect(args).toContain("--acp")
    expect(args).toContain("--harness")
    expect(args).toContain("omp")
    expect(args).toContain("--model")
    expect(args).toContain("m1")
    expect(args).toContain("--effort")
    expect(args).toContain("high")
  })
})
