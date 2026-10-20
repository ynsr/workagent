import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { Zap } from "lucide-react"
import { ActionIcon } from "@/components/ActionIcon"
import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { errorText } from "@/components/StatusFeedback"
import { useActions, useCreateRun } from "@/lib/queries"

/** Dashboard top-bar dropdown: run a custom action on each selected
 * worktree (one run per worktree). Disabled until ≥1 row is selected. */
export function ActionsMenu({ selected, compact = false }: { selected: Set<string>; compact?: boolean }) {
  const navigate = useNavigate()
  const createRun = useCreateRun()
  const { data } = useActions()
  const actions = data?.actions ?? []
  const [open, setOpen] = useState(false)
  const [running, setRunning] = useState<string | null>(null)

  async function handleRun(label: string) {
    setRunning(label)
    setOpen(false)
    for (const key of [...selected].sort()) {
      try {
        const { run_id } = await createRun.mutateAsync({
          command: "action",
          args: ["run", label, key, "--launch", "--yes"],
          confirm: true,
        })
        toast.success(`Action ${label} on ${key} started`, {
          action: { label: "View", onClick: () => navigate(`/runs/${run_id}`) },
        })
      } catch (err) {
        toast.error(`${key}: ${errorText(err)}`)
      }
    }
    setRunning(null)
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        {compact ? (
          <Button
            variant="outline"
            size="sm"
            disabled={selected.size === 0}
            title={
              selected.size > 0
                ? `Run a custom action on ${[...selected][0]}`
                : "No worktree"
            }
          >
            <Zap aria-hidden className="size-4" />
            Actions
          </Button>
        ) : (
        <Button
          variant="outline"
          size="sm"
          disabled={selected.size === 0}
          title={
            selected.size > 0
              ? `Run a custom action on ${selected.size} selected worktrees`
              : "Select one or more worktrees to run a custom action"
          }
        >
          <Zap aria-hidden />
          {selected.size > 0 ? `Actions (${selected.size})` : "Actions"}
        </Button>
        )}
      </PopoverTrigger>
      <PopoverContent className="w-64 p-1" align="end">
        {actions.length === 0 ? (
          <p className="px-2 py-3 text-sm text-muted-foreground">
            No custom actions yet.{" "}
            <a href="/actions" className="underline underline-offset-2">Add one</a>.
          </p>
        ) : (
          actions.map((a) => (
            <button
              key={a.label}
              type="button"
              disabled={running !== null}
              onClick={() => void handleRun(a.label)}
              className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm hover:bg-accent"
              title={`Run ${a.name} on ${selected.size} selected worktrees (headless)`}
            >
              <ActionIcon name={a.icon} className="size-4 shrink-0" />
              <span className="min-w-0 flex-1 truncate">{a.name}</span>
              {running === a.label ? (
                <span className="text-xs text-muted-foreground">…</span>
              ) : null}
            </button>
          ))
        )}
      </PopoverContent>
    </Popover>
  )
}
