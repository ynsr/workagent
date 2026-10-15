import { Rocket } from "lucide-react"
import { Button } from "@/components/ui/button"
import { RefreshOverlay } from "@/components/StatusFeedback"
import { useConfirmedRun } from "@/components/RunActions"
import { useReleaseStatus } from "@/lib/queries"

/** Shared Release overlay button: disabled with reason when not releasable, never hidden. */
export function ReleaseButton({ repo, compact }: { repo: string; compact?: boolean }) {
  const { data, isPending, isFetching } = useReleaseStatus(repo)
  const runConfirmed = useConfirmedRun()
  const releasable = data?.releasable === true
  const reason = data?.reason || (isPending ? "loading…" : "not releasable")
  const title = releasable && data
    ? `Release ${data.default_branch} → ${data.release_branch} (${data.diff_count} commits)`
    : reason

  async function handleRelease() {
    if (!data || !releasable) return
    await runConfirmed({
      action: "release",
      title: `Release ${data.default_branch} → ${data.release_branch}`,
      description: `Open a release MR with FF-only auto-merge (${data.diff_count} commits).`,
      confirmLabel: "Release",
      details: [
        { label: "Repo", value: repo, mono: true },
        { label: "Default", value: data.default_branch, mono: true },
        { label: "Release", value: data.release_branch, mono: true },
        { label: "Commits", value: String(data.diff_count), mono: true },
      ],
      command: "release",
      args: [repo, "--yes"],
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
        disabled={!releasable}
        onClick={() => void handleRelease()}
        className="text-muted-foreground hover:bg-primary/10 hover:text-primary disabled:opacity-40"
      >
        <Rocket aria-hidden />
        {compact ? null : "Release"}
      </Button>
    </span>
  )
}
