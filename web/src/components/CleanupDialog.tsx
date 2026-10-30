import { useState } from "react"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { CheckRow } from "@/components/FieldHelp"

/**
 * Remove-worktree ("cleanup") confirmation with a live outcome table.
 * Local `force` state drives the "What happens" column, so toggling force
 * updates the table instantly (a shared confirm() extras node would be a
 * stale snapshot captured at open time).
 */
export const LONG_LIVED_SOURCES = ["dev", "develop", "main", "master"] as const

export function isLongLivedSource(branch: string): boolean {
  return (LONG_LIVED_SOURCES as readonly string[]).includes(
    (branch || "").trim().toLowerCase(),
  )
}

export function CleanupDialog({
  worktreeKey,
  invalid,
  deleted = false,
  sourceBranch,
  onClose,
}: {
  worktreeKey: string
  invalid: boolean
  deleted?: boolean
  sourceBranch?: string
  onClose: (ok: false | { force: boolean; hardDelete: boolean } | { action: "deactivate" } | { action: "deleteFromDb"; hardDelete: boolean } | { action: "undelete" } | { action: "destroy" }) => void
}): React.JSX.Element {
  const [force, setForce] = useState(invalid)
  const [hardDelete, setHardDelete] = useState(false)
  const effectiveForce = invalid || force
  const longLived = isLongLivedSource(sourceBranch ?? "")
  const rows: [string, string][] = invalid || deleted
    ? [["State check", deleted ? "Row is already soft-deleted — restoring or purging, no merge attempted" : "Skipped — entry is removed either way"]]
    : [
        [
          "Mergeable PR",
          longLived
            ? "Merge commit (long-lived source keeps history), then delete the remote branch"
            : "Squash-merge, then delete the remote branch",
        ],
        [
          "Conflicted PR",
          effectiveForce
            ? "Merge attempted — left open, remote kept, rest cleaned"
            : "Aborts (exit 1) — nothing is torn down",
        ],
        [
          "Merged PR",
          "Local cleanup, then delete the remote branch",
        ],
        [
          "PR missing on host (404)",
          "Aborts — link and remote branch kept",
        ],
      ]

  return (
    <AlertDialog open onOpenChange={(open) => !open && onClose(false)}>
      <AlertDialogContent className="max-w-2xl">
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            <span
              aria-hidden
              className="inline-block h-2.5 w-2.5 rounded-full bg-destructive"
            />
            {invalid ? `Delete invalid worktree ${worktreeKey}` : deleted ? `Clean up deleted worktree ${worktreeKey}` : `Remove worktree ${worktreeKey}`}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {invalid
              ? "The recorded path is missing or not a live git worktree. The entry is removed either way. This cannot be undone."
              : deleted
                ? "This entry is already soft-deleted — the merge/close already ran (or nothing remote was ever done). Restore it, or purge the row now with Delete from DB + hard-delete (tears down worktree files and branches immediately)."
              : hardDelete
                ? "Closes the tracker issue, removes the worktree, deletes the local branch, and merges or closes the PR. The remote branch is deleted only when the PR merges. This cannot be undone."
                : "Closes or merges the PR, then soft-deletes the entry — worktree files, branch, and remote branch all survive, recoverable for 7 days via link undelete. Check hard-delete to tear everything down immediately."}
          </AlertDialogDescription>
          {!invalid && !deleted ? (
            <div className="overflow-x-auto rounded-md border">
              <table className="w-full min-w-130 text-left text-sm">
                <caption className="px-3 pt-2 text-left text-xs font-medium text-muted-foreground">
                  What will happen
                  {effectiveForce ? " with force" : ""}
                </caption>
                <thead>
                  <tr className="border-b bg-muted/40 text-xs text-muted-foreground">
                    <th scope="col" className="px-3 py-2 font-medium">
                      Situation
                    </th>
                    <th scope="col" className="px-3 py-2 font-medium">
                      What happens
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(([situation, outcome]) => (
                    <tr key={situation} className="border-b align-top last:border-0">
                      <th scope="row" className="px-3 py-2 font-medium">
                        {situation}
                      </th>
                      <td className="px-3 py-2">{outcome}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {!deleted ? (
          <div className="grid gap-2.5">
            <CheckRow
              id="cleanup-force"
              checked={invalid || force}
              onChange={(v) => setForce(v)}
              label="Skip state validation"
              flag="--force"
              description="Skip state validation and confirmation"
            />
            <CheckRow
              id="cleanup-hard-delete"
              checked={hardDelete}
              onChange={(v) => setHardDelete(v)}
              label="Hard-delete (no recovery)"
              flag="--hard-delete"
              description="Delete the row, worktree, and branch immediately — skip the 7-day recovery window"
            />
          </div>
          ) : null}
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={() => onClose(false)}>Cancel</AlertDialogCancel>
          {!invalid && !deleted ? (
            <AlertDialogAction
              title="link deactivate — hide from bulk ops; reversible, nothing on disk touched"
              onClick={() => onClose({ action: "deactivate" })}
            >
              Deactivate
            </AlertDialogAction>
          ) : null}
          {deleted ? (
            <AlertDialogAction
              title="link undelete — restore the soft-deleted row"
              onClick={() => onClose({ action: "undelete" })}
            >
              Restore
            </AlertDialogAction>
          ) : null}
          {deleted ? (
            <AlertDialogAction
              title="link destroy-deleted — tear down worktree dir + local branch + row (DB and filesystem); remote branches untouched"
              onClick={() => onClose({ action: "destroy" })}
            >
              Destroy (DB + files)
            </AlertDialogAction>
          ) : null}
          <AlertDialogAction
            title={deleted ? "link remove --hard-delete — purge the row now" : "link remove — soft-delete the database row (recoverable 7 days); files/branch/PR untouched"}
            onClick={() => onClose({ action: "deleteFromDb", hardDelete: deleted ? true : hardDelete })}
          >
            Delete from DB
          </AlertDialogAction>
          {!deleted ? (
            <AlertDialogAction
              autoFocus
              onClick={() => onClose({ force: effectiveForce, hardDelete })}
            >
              {invalid ? "Delete worktree" : "Remove worktree"}
            </AlertDialogAction>
          ) : null}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
