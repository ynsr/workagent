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
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"

/**
 * Remove-worktree ("cleanup") confirmation with a live comparison table.
 * Local `force` state drives the Effective column, so toggling --force
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
  onClose: (ok: false | { force: boolean; dry: boolean; json: boolean }) => void
}) {
  const [force, setForce] = useState(invalid)
  const [dryRun, setDryRun] = useState(false)
  const [json, setJson] = useState(false)
  const effectiveForce = (invalid || force) && !dryRun

  const rows: [string, string, string][] = invalid
    ? [["State check", "Runs", "Skipped"]]
    : [
        [
          "Conflicted PR (no --force)",
          "Aborts — nothing is torn down",
          effectiveForce ? "Skipped — merge attempted" : "Aborts (exit 1)",
        ],
        [
          "Mergeable PR",
          "Squash-merge, then delete remote branch",
          "Squash-merge, then delete remote branch",
        ],
        [
          "Unmergeable PR + --force",
          "—",
          effectiveForce
            ? "Left open, remote kept, rest cleaned"
            : "Aborts (exit 1)",
        ],
        [
          "Merged PR",
          "Local cleanup, delete remote branch",
          "Local cleanup, delete remote branch",
        ],
        [
          "404 PR on close",
          "Aborts — link and remote kept",
          "Aborts — link and remote kept",
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
                  Cleanup vs --force
                  {effectiveForce ? " — showing --force behavior" : ""}
                </caption>
                <thead>
                  <tr className="border-b bg-muted/40 text-xs text-muted-foreground">
                    <th scope="col" className="px-3 py-2 font-medium">
                      Situation
                    </th>
                    <th scope="col" className="px-3 py-2 font-medium">
                      Cleanup
                    </th>
                    <th scope="col" className="px-3 py-2 font-medium">
                      With --force
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(([situation, plain, forced]) => (
                    <tr key={situation} className="border-b align-top last:border-0">
                      <th scope="row" className="px-3 py-2 font-medium">
                        {situation}
                      </th>
                      <td className="px-3 py-2 text-muted-foreground">{plain}</td>
                      <td className="px-3 py-2">{forced}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          <div className="grid gap-2.5">
            <div className="flex items-start gap-2">
              <Checkbox
                id="cleanup-force"
                checked={invalid || (force && !dryRun)}
                disabled={invalid || dryRun}
                onCheckedChange={(v) => setForce(v === true)}
                className="mt-0.5"
              />
              <Label htmlFor="cleanup-force" className="text-sm font-normal leading-snug">
                <span className="font-mono text-[13px]">--force</span>
                {" — skip state validation and confirmation"}
              </Label>
            </div>
            <div className="flex items-start gap-2">
              <Checkbox
                id="cleanup-dry"
                checked={dryRun}
                onCheckedChange={(v) => {
                  const d = v === true
                  setDryRun(d)
                  if (d) setForce(false)
                }}
                className="mt-0.5"
              />
              <Label htmlFor="cleanup-dry" className="text-sm font-normal leading-snug">
                <span className="font-mono text-[13px]">--dry-run</span>
                {" — print the plan without acting"}
              </Label>
            </div>
            <div className="flex items-start gap-2">
              <Checkbox
                id="cleanup-json"
                checked={json}
                onCheckedChange={(v) => setJson(v === true)}
                className="mt-0.5"
              />
              <Label htmlFor="cleanup-json" className="text-sm font-normal leading-snug">
                <span className="font-mono text-[13px]">--json</span>
                {" — JSON output in the run log"}
              </Label>
            </div>
          </div>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={() => onClose(false)}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            autoFocus
            onClick={() => onClose({ force: effectiveForce, dry: dryRun, json })}
            className="bg-destructive text-white hover:bg-destructive/90"
          >
            {invalid ? "Delete worktree" : "Remove worktree"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
