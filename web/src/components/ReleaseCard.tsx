import { Rocket } from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { CiBadge } from "@/components/StateBadge"
import { EmptyState, ErrorState, RefreshOverlay } from "@/components/StatusFeedback"
import { useRepos, useReleaseStatus, useReleaseStatuses } from "@/lib/queries"
import { ReleaseButton } from "@/components/ReleaseButton"

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
      <ReleaseButton repo={data.repo} />
    </div>
  )
}
