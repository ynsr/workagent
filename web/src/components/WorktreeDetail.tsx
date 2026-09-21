import { useState } from "react"
import type { WorktreeEntry } from "@/lib/api"
import { prLabel } from "@/lib/api"
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
  mode,
  onSave,
  onClose,
}: {
  entry: WorktreeEntry
  mode: WorktreeDetailMode
  onSave: (draft: WorktreeDetailDraft) => void
  onClose: () => void
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
      <CardHeader>
        <CardTitle className="font-mono text-[13px]">{entry.branch ?? "—"}</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="space-y-1.5 text-sm">
          <div className="flex items-baseline gap-2">
            <dt className="w-16 shrink-0 text-xs text-muted-foreground">Branch</dt>
            <dd className="min-w-0 truncate font-mono text-[13px]" title={entry.branch}>
              {entry.branch ?? "—"}
            </dd>
          </div>
          {entry.harness ? (
            <div className="flex items-baseline gap-2">
              <dt className="w-16 shrink-0 text-xs text-muted-foreground">Harness</dt>
              <dd
                className="min-w-0 truncate font-mono text-[13px] text-muted-foreground"
                title="live harness (name, pid)"
              >
                {entry.harness}
              </dd>
            </div>
          ) : null}
          <div className="flex items-baseline gap-2">
            <dt className="w-16 shrink-0 text-xs text-muted-foreground">Path</dt>
            <dd className="min-w-0 truncate font-mono text-[13px]" title={entry.worktree}>
              {entry.worktree ?? "—"}
            </dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="w-16 shrink-0 text-xs text-muted-foreground">Repo</dt>
            <dd className="min-w-0 truncate font-mono text-[13px]" title={entry.repo}>
              {entry.repo ?? "—"}
            </dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="w-16 shrink-0 text-xs text-muted-foreground">PR</dt>
            <dd className="min-w-0 truncate">
              {entry.pr ? (
                <a
                  href={entry.pr}
                  target="_blank"
                  rel="noreferrer"
                  className="underline-offset-2 hover:underline"
                  title={entry.pr}
                >
                  {prLabel(entry.pr)}
                </a>
              ) : (
                <span className="text-muted-foreground">—</span>
              )}
            </dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="w-16 shrink-0 text-xs text-muted-foreground">CI</dt>
            <dd>
              <CiBadge ci={entry.ci} />
            </dd>
          </div>
        </dl>
      </CardContent>
      <CardFooter className="justify-end">
        <Button variant="outline" onClick={onClose}>
          Close
        </Button>
      </CardFooter>
    </Card>
  )
}
