import { useState } from "react"
import { toast } from "sonner"
import { Copy, FolderOpen, History, Rocket } from "lucide-react"
import type { WorktreeEntry } from "@/lib/api"
import { prLabel, prUrl } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { CiBadge } from "@/components/StateBadge"
import { CopyCell } from "@/components/CopyCell"
import { copyToClipboard } from "@/lib/format"
import { errorText } from "@/components/StatusFeedback"
/** Editable subset rendered in edit mode; strings are trimmed by the caller. */
export interface WorktreeDetailDraft {
  branch: string
  worktree: string
  repo: string
  pr: string
}

export type WorktreeDetailMode = "view" | "edit"

/**
 * Detail card for one worktree entry. View mode shows text; edit mode binds
 * Inputs to a local draft and calls `onSave(draft)` (Save) / `onClose()`
 * (Cancel). Field names follow `StatusTable.tsx` / `WorktreeEntry`.
 */
export function WorktreeDetail({
  entry,
  worktreeKey,
  mode,
  onSave,
  onClose,
  onOpenWorktree,
  onOpenRun,
  onOpenSessions,
  rowActions,
  networkExposed = false,
}: {
  entry: WorktreeEntry
  worktreeKey?: string
  mode: WorktreeDetailMode
  onSave: (draft: WorktreeDetailDraft) => void
  onClose: () => void
  onOpenWorktree?: (key: string) => void
  onOpenRun?: (key: string) => void
  onOpenSessions?: (key: string) => void
  /** Row actions rendered as the dialog "Actions" row (Sync/Review/Fix/…); omitted in edit mode. */
  rowActions?: React.ReactNode
  networkExposed?: boolean
}) {
  const [draft, setDraft] = useState<WorktreeDetailDraft>(() => ({
    branch: entry.branch ?? "",
    worktree: entry.worktree ?? "",
    repo: entry.repo ?? "",
    pr: entry.pr ?? "",
  }))

  const update = (field: keyof WorktreeDetailDraft, value: string) =>
    setDraft((d) => ({ ...d, [field]: value }))

  if (mode === "edit") {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="font-mono text-[13px]">{entry.branch ?? "—"}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="wd-branch">Branch</Label>
              <Input
                id="wd-branch"
                value={draft.branch}
                onChange={(e) => update("branch", e.target.value)}
                placeholder="main"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="wd-worktree">Worktree</Label>
              <Input
                id="wd-worktree"
                value={draft.worktree}
                onChange={(e) => update("worktree", e.target.value)}
                placeholder="/path/to/worktree"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="wd-repo">Repo</Label>
              <Input
                id="wd-repo"
                value={draft.repo}
                onChange={(e) => update("repo", e.target.value)}
                placeholder="owner/repo"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="wd-pr">PR / MR URL</Label>
              <Input
                id="wd-pr"
                value={draft.pr}
                onChange={(e) => update("pr", e.target.value)}
                placeholder="https://…"
              />
            </div>
          </div>
        </CardContent>
        <CardFooter className="justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => onSave(draft)}>Save</Button>
        </CardFooter>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader className="min-w-0 overflow-hidden">
        <div className="flex min-w-0 items-center gap-2 overflow-hidden">
          <CardTitle className="min-w-0 flex-1 truncate overflow-hidden font-mono text-[13px]" title={entry.branch ?? undefined}>{entry.branch ?? "—"}</CardTitle>
        </div>
      </CardHeader>
      <CardContent>
        <dl className="space-y-1.5 text-sm">
          <div className="flex items-baseline gap-2">
            <dt className="w-16 shrink-0 text-xs text-muted-foreground">Branch</dt>
            <dd className="min-w-0 flex-1 font-mono text-[13px]">
              <CopyCell text={entry.branch} />
            </dd>
          </div>
          {entry.harness ? (
            <div className="flex items-baseline gap-2">
              <dt className="w-16 shrink-0 text-xs text-muted-foreground">Harness</dt>
              <dd className="min-w-0 flex-1 font-mono text-[13px] text-muted-foreground">
                <CopyCell text={entry.harness}
                  title={entry.harness.startsWith("terminal ")
                    ? "terminal agent (pid) — stoppable from the Dashboard row"
                    : "live harness (name, pid)"} />
              </dd>
            </div>
          ) : null}
          <div className="flex items-baseline gap-2">
            <dt className="w-16 shrink-0 text-xs text-muted-foreground">Path</dt>
            <dd className="min-w-0 flex-1 font-mono text-[13px]">
              <CopyCell text={entry.worktree} />
            </dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="w-16 shrink-0 text-xs text-muted-foreground">Repo</dt>
            <dd className="min-w-0 flex-1 font-mono text-[13px]">
              <CopyCell text={entry.repo} />
            </dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="w-16 shrink-0 text-xs text-muted-foreground">PR</dt>
            <dd className="min-w-0 truncate">
              {prUrl(entry) ? (
                <a
                  href={prUrl(entry)}
                  target="_blank"
                  rel="noreferrer"
                  className="underline-offset-2 hover:underline"
                  title={prUrl(entry)}
                >
                  {prLabel(prUrl(entry))}
                </a>
              ) : (
                <span className="text-muted-foreground">—</span>
              )}
            </dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="w-16 shrink-0 text-xs text-muted-foreground">CI</dt>
            <dd>
              <CiBadge ci={entry.ci} ciUrl={entry.ci_url} prUrl={prUrl(entry)} />
            </dd>
          </div>
          {rowActions ? (
            <div className="flex items-baseline gap-2">
              <dt className="w-16 shrink-0 text-xs text-muted-foreground">Actions</dt>
              <dd className="min-w-0 flex-1">
                <div className="[&_button]:size-8">{rowActions}</div>
              </dd>
            </div>
          ) : null}
          {entry.reviews_detail ? (
            <div className="flex items-baseline gap-2">
              <dt className="w-16 shrink-0 text-xs text-muted-foreground">Reviews</dt>
              <dd
                className="font-mono text-[13px]"
                title={`${entry.reviews_detail.reviews} completed review passes, ${entry.reviews_detail.unresolved} unresolved comments, ${entry.reviews_detail.resolved} resolved comments`}
              >
                {entry.reviews_detail.reviews} done / {entry.reviews_detail.unresolved} unresolved /{" "}
                {entry.reviews_detail.resolved} resolved
              </dd>
            </div>
          ) : null}
        </dl>
      </CardContent>
      <CardFooter className="flex flex-wrap items-center gap-2">
        {worktreeKey && onOpenWorktree ? (
          <Button
            variant="outline"
            size="sm"
            aria-label={
              networkExposed
                ? `Cannot open ${worktreeKey}: disabled while the server is network-exposed`
                : `Open worktree folder of ${worktreeKey}`
            }
            title={
              networkExposed
                ? `Cannot open ${worktreeKey}: disabled while the server is network-exposed`
                : `Open worktree folder of ${worktreeKey}`
            }
            disabled={networkExposed}
            onClick={() => onOpenWorktree(worktreeKey)}
          >
            <FolderOpen aria-hidden className="size-4" />
            Open
          </Button>
        ) : null}
        <Button
          variant="outline"
          size="sm"
          aria-label={entry.worktree ? `Copy worktree path ${entry.worktree}` : "Nothing to copy"}
          title={entry.worktree ? `Copy worktree path ${entry.worktree}` : "Nothing to copy"}
          disabled={!entry.worktree?.trim()}
          onClick={() => {
            void copyToClipboard(entry.worktree ?? "")
              .then(() => toast.success("Worktree path copied"))
              .catch((err: unknown) => toast.error(errorText(err)))
          }}
        >
          <Copy aria-hidden className="size-4" />
          Path
        </Button>
        {worktreeKey && onOpenRun ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenRun(worktreeKey)}
          >
            <Rocket aria-hidden className="size-4" />
            Runs
          </Button>
        ) : null}
        {worktreeKey && onOpenSessions ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenSessions(worktreeKey)}
          >
            <History aria-hidden className="size-4" />
            Sessions
          </Button>
        ) : null}
        <span className="flex-1" />
        <Button variant="outline" size="sm" onClick={onClose}>
          Close
        </Button>
      </CardFooter>
    </Card>
  )
}
