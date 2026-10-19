import { useState } from "react"
import { Workflow } from "lucide-react"
import { FlowWizard } from "@/components/FlowWizard"
import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import type { FlowTemplateRow } from "@/lib/api"
import { useFlowTemplates } from "@/lib/queries"

/** Dashboard top-bar dropdown: run a flow template on each selected
 * worktree (one run per worktree). Disabled until ≥1 row is selected. */
export function FlowsMenu({ selected }: { selected: Set<string> }) {
  const { data } = useFlowTemplates()
  const templates = data?.templates ?? []
  const [open, setOpen] = useState(false)
  const [wizard, setWizard] = useState<FlowTemplateRow | null>(null)

  function handlePick(template: FlowTemplateRow) {
    setOpen(false)
    setWizard(template)
  }

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            disabled={selected.size === 0}
            title={
              selected.size > 0
                ? `Run a flow template on ${selected.size} selected worktrees`
                : "Select one or more worktrees to run a flow template"
            }
          >
            <Workflow aria-hidden />
            {selected.size > 0 ? `Flows (${selected.size})` : "Flows"}
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-64 p-1" align="end">
          {templates.length === 0 ? (
            <p className="px-2 py-3 text-sm text-muted-foreground">
              No flow templates yet.{" "}
              <a href="/flow-templates" className="underline underline-offset-2">Add one</a>.
            </p>
          ) : (
            templates.map((t) => (
              <button
                key={t.label}
                type="button"
                onClick={() => handlePick(t)}
                className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm hover:bg-accent"
                title={`Run ${t.name} on ${selected.size} selected worktrees`}
              >
                <Workflow aria-hidden className="size-4 shrink-0" />
                <span className="min-w-0 flex-1 truncate">{t.name}</span>
                <span className="shrink-0 font-mono text-xs text-muted-foreground">
                  {t.steps?.length ?? 0}
                </span>
              </button>
            ))
          )}
        </PopoverContent>
      </Popover>
      {wizard ? (
        <FlowWizard
          template={wizard}
          refs={[...selected].sort()}
          onClose={() => setWizard(null)}
        />
      ) : null}
    </>
  )
}
