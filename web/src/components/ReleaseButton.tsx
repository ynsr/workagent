import { useState } from "react"
import { ChevronDown, Rocket } from "lucide-react"
import { Button } from "@/components/ui/button"
import { RefreshOverlay } from "@/components/StatusFeedback"
import { useConfirmedRun } from "@/components/RunActions"
import { useReleaseStatus } from "@/lib/queries"
import {
  CHECK_OPTIONS,
  DIFF_OPTIONS,
  loadPrefs,
  savePrefs,
  type Checks,
  type DiffMode,
} from "@/lib/releasePrefs"

/** Release split button: main runs with last-used prefs; chevron picks checks+diff. */
export function ReleaseButton({ repo, compact }: { repo: string; compact?: boolean }) {
  const { data, isPending, isFetching } = useReleaseStatus(repo)
  const runConfirmed = useConfirmedRun()
  const [menuOpen, setMenuOpen] = useState(false)
  const [prefs, setPrefs] = useState(loadPrefs)
  function pick(checks: Checks, diff: DiffMode) {
    setPrefs({ checks, diff })
    savePrefs(checks, diff)
    setMenuOpen(false)
  }
  const releasable = data?.releasable === true
  const releasing = data?.releasing === true
  const reason = releasing
    ? `release already running for ${repo}`
    : data?.reason || (isPending ? "loading…" : "not releasable")
  const title = releasing
    ? reason
    : releasable && data
      ? `Release ${data.default_branch} → ${data.release_branch} (${data.diff_count} commits)`
      : reason
  async function handleRelease() {
    if (!data || !releasable) return
    const { checks, diff } = prefs
    await runConfirmed({
      action: "release",
      title: `Release ${data.default_branch} → ${data.release_branch}`,
      description: `Merge the pinned default-branch SHA via MR (checks=${checks}, diff=${diff}; strategy chosen at run time).`,
      confirmLabel: "Release",
      details: [
        { label: "Repo", value: repo, mono: true },
        { label: "Default", value: data.default_branch, mono: true },
        { label: "Release", value: data.release_branch, mono: true },
        { label: "Commits", value: String(data.diff_count), mono: true },
        { label: "Checks", value: checks, mono: true },
        { label: "Diff", value: diff, mono: true },
      ],
      command: "release",
      args: [repo, "--checks", checks, "--diff", diff, "--yes"],
      successLabel: "released",
    })
  }

  return (
    <span className="relative inline-flex">
      {isFetching && !isPending ? <RefreshOverlay /> : null}
      <Button
        variant="ghost"
        size={compact ? "icon" : "sm"}
        aria-label={`Release ${repo}`}
        title={title}
        disabled={!releasable || releasing}
        onClick={() => void handleRelease()}
        className="rounded-r-none text-muted-foreground hover:bg-primary/10 hover:text-primary disabled:opacity-40"
      >
        <Rocket aria-hidden />
        {compact ? null : "Release"}
      </Button>
      <Button
        variant="ghost"
        size="icon"
        aria-label={`Release options for ${repo}`}
        title={releasing ? reason : `Checks: ${prefs.checks}, diff: ${prefs.diff}`}
        disabled={!releasable || releasing}
        onClick={() => setMenuOpen((v) => !v)}
        className="rounded-l-none border-l border-border/50 px-1 text-muted-foreground hover:bg-primary/10 hover:text-primary disabled:opacity-40"
      >
        <ChevronDown aria-hidden />
      </Button>
      {menuOpen && releasable ? (
        <span
          role="menu"
          aria-label="Release options"
          className="absolute right-0 top-full z-50 mt-1 w-44 rounded-md border border-border bg-popover p-1 shadow-md"
          onKeyDown={(e) => { if (e.key === "Escape") setMenuOpen(false) }}
        >
          <span className="block px-2 py-1 text-xs font-medium text-muted-foreground">Checks</span>
          {CHECK_OPTIONS.map((c) => (
            <button
              key={c}
              type="button"
              role="menuitemradio"
              aria-checked={prefs.checks === c}
              onClick={() => pick(c, prefs.diff)}
              className={`block w-full rounded-sm px-2 py-1 text-left text-sm hover:bg-accent ${prefs.checks === c ? "font-semibold" : ""}`}
            >
              {c}
            </button>
          ))}
          <span className="block px-2 py-1 text-xs font-medium text-muted-foreground">Diff</span>
          {DIFF_OPTIONS.map((d) => (
            <button
              key={d}
              type="button"
              role="menuitemradio"
              aria-checked={prefs.diff === d}
              onClick={() => pick(prefs.checks, d)}
              className={`block w-full rounded-sm px-2 py-1 text-left text-sm hover:bg-accent ${prefs.diff === d ? "font-semibold" : ""}`}
            >
              {d}
            </button>
          ))}
        </span>
      ) : null}
    </span>
  )
}
