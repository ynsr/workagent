import { useState } from "react"
import { Rocket, Wrench } from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { CiBadge } from "@/components/StateBadge"
import { EmptyState, ErrorState, RefreshOverlay } from "@/components/StatusFeedback"
import { useRepos, useReleaseStatus, useReleaseStatuses } from "@/lib/queries"
import { useConfirmedRun } from "@/components/RunActions"
import { ActionDialog } from "@/components/LinkDialogs"
import { RunModeSelect } from "@/components/StartForm"
import type { RunMode } from "@/lib/launchConfig"
import { extraPromptArgs } from "@/lib/launchConfig"
import type { ReleaseStatus } from "@/lib/api"
import { ReleaseButton } from "@/components/ReleaseButton"
import { FieldHelp } from "@/components/FieldHelp"

/** Blinking green dot while a release holds the repo's lock. */
export function ReleasingDot({ repo }: { repo: string }) {
  const { data } = useReleaseStatus(repo)
  if (!data?.releasing) return null
  return (
    <span
      aria-label={`Release running for ${repo}`}
      title={`Release running for ${repo}`}
      className="size-2 shrink-0 animate-pulse rounded-full bg-emerald-500"
    />
  )
}

function relativeFetchMs(iso: string | null | undefined): string {
  if (!iso) return "—"
  const ms = Date.parse(iso)
  if (Number.isNaN(ms)) return "—"
  const diff = Date.now() - ms
  if (diff < 10_000) return "just now"
  const s = Math.floor(diff / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  if (d < 7) return `${d}d ago`
  return new Date(ms).toLocaleString()
}

export function ReleaseCard() {
  const { data: repos, isPending, isError, isFetching } = useRepos()
  if (isPending && !repos) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Release</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">Loading…</p>
        </CardContent>
      </Card>
    )
  }
  if (isError && !repos) {
    return <ErrorState error={isError} onRetry={() => window.location.reload()} />
  }
  if (!repos || repos.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Release</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState
            icon={<Rocket aria-hidden />}
            title="No repos registered"
            description="Register a repo with a release branch to enable releases."
          />
        </CardContent>
      </Card>
    )
  }
  const branched = repos.filter((r) => r.release_branch)
  if (branched.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Ready to release</CardTitle>
          <CardDescription>
            No registered repo has a release branch yet.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <EmptyState
            icon={<Rocket aria-hidden />}
            title="No release branches"
            description="Set one via repo update NAME --release-branch X."
          />
        </CardContent>
      </Card>
    )
  }
  return (
    <Card className="relative">
      <CardHeader>
        <CardTitle>
          Ready to release <ReleaseReadyCount names={branched.map((r) => r.name)} />
        </CardTitle>
        <CardDescription>
          Repos with commits on the default branch ahead of the release branch.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {branched.map((r) => (
          <ReleaseRow key={r.name} name={r.name} />
        ))}
      </CardContent>
      {isFetching ? <RefreshOverlay label="Checking repos…" /> : null}
    </Card>
  )
}

function ReleaseReadyCount({ names }: { names: string[] }) {
  const statuses = useReleaseStatuses(names)
  const n = statuses.filter((s) => s.data?.releasable).length
  return n ? <span>({n})</span> : null
}

function ReleaseRow({ name }: { name: string }) {
  const { data, isPending, isError } = useReleaseStatus(name)
  const [fixOpen, setFixOpen] = useState(false)
  if (isPending && !data) {
    return (
      <div className="flex items-center justify-between rounded-lg border p-3">
        <span className="font-medium">{name}</span>
        <span className="text-sm text-muted-foreground">Checking…</span>
      </div>
    )
  }
  if (isError && !data) {
    return (
      <div className="flex items-center justify-between rounded-lg border p-3">
        <span className="font-medium">{name}</span>
        <span className="text-sm text-destructive">Failed to load</span>
      </div>
    )
  }
  if (!data?.releasable) {
    return null
  }
  return (
    <div className="relative flex items-center justify-between gap-3 rounded-lg border p-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-medium">{data.repo}</span>
          <ReleasingDot repo={data.repo} />
          {data.ci && data.ci !== "not_started" ? (
            <CiBadge ci={data.ci as "success" | "failure" | "running"} ciUrl={data.ci_url || undefined} />
          ) : null}
        </div>
        <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
          {data.default_branch} → {data.release_branch} · {data.diff_count} commit
          {data.diff_count === 1 ? "" : "s"} ahead
          {data.stale ? " · stale" : ""}
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {data.fetched_at ? `Checked ${relativeFetchMs(data.fetched_at)}` : ""}
        </p>
      </div>
      <span className="inline-flex items-center gap-1">
        {(data.behind ?? 0) > 0 || data.ci === "failure" ? (
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Fix default branch for ${data.repo}`}
            title={
              (data.behind ?? 0) > 0 && data.ci === "failure"
                ? `Default is ${data.behind} behind ${data.release_branch} and CI is failing`
                : (data.behind ?? 0) > 0
                  ? `Default is ${data.behind} behind ${data.release_branch}`
                  : "Default-branch CI is failing"
            }
            onClick={() => setFixOpen(true)}
            className="text-muted-foreground hover:bg-primary/10 hover:text-primary"
          >
            <Wrench aria-hidden /> Fix default
          </Button>
        ) : null}
        <ReleaseButton repo={data.repo} />
      </span>
      {fixOpen ? <FixDefaultDialog status={data} onClose={() => setFixOpen(false)} /> : null}
    </div>
  )
}
type FixAction = "rebase" | "ci" | "both"

const FIX_ACTION_OPTIONS: readonly { value: FixAction; label: string; hint: string }[] = [
  { value: "both", label: "Both", hint: "--action both — rebase onto release, then fix CI" },
  { value: "rebase", label: "Rebase", hint: "--action rebase — rebase default onto release" },
  { value: "ci", label: "CI", hint: "--action ci — fix the failing default-branch CI" },
]

/** Fix-default dialog: rebase default onto release and/or fix its CI via the harness. */
export function FixDefaultDialog({ status, onClose }: { status: ReleaseStatus; onClose: () => void }) {
  const runConfirmed = useConfirmedRun()
  const [action, setAction] = useState<FixAction>("both")
  const [runMode, setRunMode] = useState<RunMode>("preview")
  const [extra, setExtra] = useState("")
  const touchesRebase = action !== "ci"
  async function handleFix() {
    // fix-default takes --run-mode (preview|headless|terminal), not the
    // --launch/--terminal pair start/review use (both rejected here).
    const args = ["fix-default", status.repo, "--action", action, "--run-mode", runMode]
    const trimmed = extra.trim()
    if (trimmed) args.push(...extraPromptArgs({ extraPrompt: trimmed }))
    onClose()
    await runConfirmed({
      action: "release",
      title: `Fix default for ${status.repo}`,
      description: touchesRebase
        ? `Rebases ${status.default_branch} onto ${status.release_branch} (force-push — rewrites origin/${status.default_branch}) and/or fixes its CI via the harness.`
        : `Fixes the failing CI on ${status.default_branch} via the harness.`,
      warning: touchesRebase
        ? `Rebasing rewrites origin/${status.default_branch} and requires a force-push.`
        : undefined,
      confirmLabel: "Fix default",
      details: [
        { label: "Repo", value: status.repo, mono: true },
        { label: "Default", value: status.default_branch, mono: true },
        { label: "Release", value: status.release_branch, mono: true },
        { label: "Action", value: action, mono: true },
        { label: "Run mode", value: runMode, mono: true },
      ],
      command: "release",
      args,
      successLabel: "started",
    })
  }
  return (
    <ActionDialog
      title={`Fix default for ${status.repo}`}
      description={
        touchesRebase
          ? `Rebase ${status.default_branch} onto ${status.release_branch} (force-push — rewrites the default branch) and/or fix its CI.`
          : `Fix the failing CI on ${status.default_branch}.`
      }
      onClose={onClose}
      footer={
        <Button type="button" onClick={() => void handleFix()}>
          <Wrench aria-hidden /> Fix default
        </Button>
      }
    >
      <div className="grid gap-1">
        <Label>
          <FieldHelp
            label="Action"
            flag="--action rebase|ci|both"
            description="Rebase default onto release, fix its failing CI, or both."
          />
        </Label>
        <div
          role="radiogroup"
          aria-label="Fix-default action"
          className="flex w-fit divide-x overflow-hidden rounded-md border text-sm"
        >
          {FIX_ACTION_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              id={`fix-default-action-${opt.value}`}
              role="radio"
              aria-checked={action === opt.value}
              title={opt.hint}
              onClick={() => setAction(opt.value)}
              className={action === opt.value
                ? "bg-primary px-3 py-1.5 font-medium text-primary-foreground"
                : "bg-transparent px-3 py-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>
      <RunModeSelect idPrefix="fix-default" value={runMode} onChange={setRunMode} />
      <div className="grid gap-2">
        <Label htmlFor="fix-default-extra">
          <FieldHelp
            label="Extra prompt"
            flag="--extra-prompt"
            description="Extra instructions appended to the fix-default harness prompt."
          />
        </Label>
        <Input
          id="fix-default-extra"
          value={extra}
          onChange={(e) => setExtra(e.target.value)}
          placeholder="Optional extra instructions for the agent"
        />
      </div>
    </ActionDialog>
  )
}
