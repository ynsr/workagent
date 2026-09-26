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
export function CleanupDialog({
  worktreeKey,
  invalid,
  onClose,
}: {
  worktreeKey: string
  invalid: boolean
  onClose: (ok: false | { force: boolean } | { action: "deactivate" } | { action: "deleteFromDb" }) => void
}) {
  const [force, setForce] = useState(invalid)
  const effectiveForce = invalid || force

  const rows: [string, string][] = invalid
    ? [["State check", "Skipped — entry is removed either way"]]
    : [
        [
          "Mergeable PR",
          "Squash-merge, then delete the remote branch",
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
            {invalid ? `Delete invalid worktree ${worktreeKey}` : `Remove worktree ${worktreeKey}`}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {invalid
              ? "The recorded path is missing or not a live git worktree. The entry is removed either way. This cannot be undone."
              : "Closes the tracker issue, removes the worktree, deletes the local branch, and merges or closes the PR. The remote branch is deleted only when the PR merges. This cannot be undone."}
          </AlertDialogDescription>
          {!invalid ? (
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
          <div className="grid gap-2.5">
            <CheckRow
              id="cleanup-force"
              checked={invalid || force}
              onChange={(v) => setForce(v)}
              label="Skip state validation"
              flag="--force"
              description="Skip state validation and confirmation"
            />
          </div>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={() => onClose(false)}>Cancel</AlertDialogCancel>
          {!invalid ? (
            <AlertDialogAction
              title="link deactivate — hide from bulk ops; reversible, nothing on disk touched"
              onClick={() => onClose({ action: "deactivate" })}
            >
              Deactivate
            </AlertDialogAction>
          ) : null}
          <AlertDialogAction
            title="link remove — drop the database row only; files/branch/PR untouched, session history deleted"
            onClick={() => onClose({ action: "deleteFromDb" })}
          >
            Delete from DB
          </AlertDialogAction>
          <AlertDialogAction
            autoFocus
            onClick={() => onClose({ force: effectiveForce })}
          >
            {invalid ? "Delete worktree" : "Remove worktree"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
