import { useState } from "react"
import { useNavigate } from "react-router-dom"
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

function startsWithStart(t: FlowTemplateRow): boolean {
  return (t.steps ?? [])[0]?.kind === "start"
}

/** Dashboard top-bar dropdown: no selection → start-first templates that
 * route to Launch → Flow tab with the template pre-selected; with a
 * selection → non-start templates that run the FlowWizard on the selected
 * worktrees (one run per worktree). Always enabled. */
export function FlowsMenu({ selected }: { selected: Set<string> }) {
  const navigate = useNavigate()
  const { data } = useFlowTemplates()
  const templates = data?.templates ?? []
  const hasSelection = selected.size > 0
  const listed = templates.filter((t) =>
    hasSelection ? !startsWithStart(t) : startsWithStart(t),
  )
  const [open, setOpen] = useState(false)
  const [wizard, setWizard] = useState<FlowTemplateRow | null>(null)

  function handlePick(template: FlowTemplateRow) {
    setOpen(false)
    if (!hasSelection) {
      navigate(`/launch?mode=flow&template=${encodeURIComponent(template.label)}`)
      return
    }
    setWizard(template)
  }

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            title={
              hasSelection
                ? `Run a flow template on ${selected.size} selected worktrees`
                : "Run a start-first flow template from the Launch page"
            }
          >
            <Workflow aria-hidden />
            {hasSelection ? `Flows (${selected.size})` : "Flows"}
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-64 p-1" align="end">
          {listed.length === 0 ? (
            <p className="px-2 py-3 text-sm text-muted-foreground">
              {hasSelection
                ? "No non-start flow templates yet. "
                : "No start-first flow templates yet. "}
              <a href="/flow-templates" className="underline underline-offset-2">Add one</a>.
            </p>
          ) : (
            listed.map((t) => (
              <button
                key={t.label}
                type="button"
                onClick={() => handlePick(t)}
                className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm hover:bg-accent"
                title={
                  hasSelection
                    ? `Run ${t.name} on ${selected.size} selected worktrees`
                    : `Open ${t.name} in Launch → Flow tab`
                }
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
