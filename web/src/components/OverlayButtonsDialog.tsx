import { useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import { CheckRow } from "@/components/FieldHelp"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ActionDialog } from "@/components/LinkDialogs"
import type { ActionRow, FlowTemplateRow } from "@/lib/api"
import {
  FIXED_OVERLAY_BUTTONS,
  dedicatedButtonId,
  formatOverlayButtons,
  parseOverlayButtons,
} from "@/lib/overlayButtons"

export type OverlayDraft = Record<string, boolean>

/**
 * Editor state: one checkbox per fixed id + per dedicated item. Stored as
 * the full {button_id: bool} map; submit serializes to `--overlay-buttons`.
 */
export function OverlayButtonFields({
  initial,
  flowTemplates,
  actionRows,
  onChange,
}: {
  initial: Record<string, boolean>
  flowTemplates: FlowTemplateRow[]
  actionRows: ActionRow[]
  onChange: (draft: OverlayDraft) => void
}) {
  const [draft, setDraft] = useState<OverlayDraft>(() => ({ ...initial }))
  const flaggedFlows = useMemo(
    () => flowTemplates.filter((t) => t.dedicated_overlay),
    [flowTemplates],
  )
  const flaggedActions = useMemo(
    () => actionRows.filter((a) => a.dedicated_overlay),
    [actionRows],
  )
  function set(id: string, show: boolean) {
    setDraft((cur) => {
      const next = { ...cur, [id]: show }
      onChange(next)
      return next
    })
  }
  return (
    <div className="grid gap-3">
      <div className="grid gap-1">
        <Label>Overlay buttons</Label>
        <p className="text-xs text-muted-foreground">
          Unchecked buttons disappear from this repo's StatusTable rows (detail panels always show all).
        </p>
      </div>
      <div className="grid gap-1.5">
        {FIXED_OVERLAY_BUTTONS.map(({ id, label }) => (
          <CheckRow
            key={id}
            id={`overlay-${id}`}
            checked={draft[id] !== false}
            onChange={(v) => set(id, v)}
            label={label}
            flag={draft[id] !== false ? id : `-${id}`}
            description={
              id === "dedicated"
                ? "master toggle: hides every dedicated action/flow button below"
                : `StatusTable overlay button (${id})`
            }
          />
        ))}
      </div>
      {flaggedFlows.length > 0 || flaggedActions.length > 0 ? (
        <div className="grid gap-1.5 border-t pt-2">
          <Label>Dedicated buttons (flagged items)</Label>
          {flaggedFlows.map((t) => {
            const id = dedicatedButtonId("flow", t.label)
            return (
              <CheckRow
                key={id}
                id={`overlay-${id}`}
                checked={draft[id] !== false}
                onChange={(v) => set(id, v)}
                label={`Flow: ${t.name}`}
                flag={id}
                description={`dedicated overlay button for flow ${t.label}`}
              />
            )
          })}
          {flaggedActions.map((a) => {
            const id = dedicatedButtonId("action", a.label)
            return (
              <CheckRow
                key={id}
                id={`overlay-${id}`}
                checked={draft[id] !== false}
                onChange={(v) => set(id, v)}
                label={`Action: ${a.name}`}
                flag={id}
                description={`dedicated overlay button for action ${a.label}`}
              />
            )
          })}
        </div>
      ) : null}
    </div>
  )
}

/** Full dialog: checkboxes + free-text fallback for unknown ids. */
export function OverlayButtonsDialog({
  repoName,
  initial,
  flowTemplates,
  actionRows,
  onClose,
  onSubmit,
}: {
  repoName: string
  initial: Record<string, boolean>
  flowTemplates: FlowTemplateRow[]
  actionRows: ActionRow[]
  onClose: () => void
  onSubmit: (overlayArg: string) => void
}) {
  const [draft, setDraft] = useState<OverlayDraft>(() => ({ ...initial }))
  const [raw, setRaw] = useState(() => formatOverlayButtons(initial))
  const [rawTouched, setRawTouched] = useState(false)
  function handleChange(next: OverlayDraft) {
    setDraft(next)
    if (!rawTouched) setRaw(formatOverlayButtons(next))
  }
  return (
    <ActionDialog
      title={`Overlay buttons for ${repoName}`}
      description="Pick which StatusTable overlay buttons this repo's rows show. Empty = all visible."
      onClose={onClose}
      footer={
        <Button
          onClick={() => onSubmit(rawTouched ? raw : formatOverlayButtons(draft))}
        >
          Update overlay buttons
        </Button>
      }
    >
      <OverlayButtonFields
        initial={initial}
        flowTemplates={flowTemplates}
        actionRows={actionRows}
        onChange={handleChange}
      />
      <div className="grid gap-2">
        <Label htmlFor="overlay-raw">Raw ids (--overlay-buttons value)</Label>
        <Input
          id="overlay-raw"
          value={raw}
          onChange={(e) => {
            setRaw(e.target.value)
            setRawTouched(true)
            try {
              setDraft(parseOverlayButtons(e.target.value))
            } catch {
              /* keep the text; the CLI validates on submit */
            }
          }}
          placeholder="sync,review,-adhoc,flow:deploy"
          autoComplete="off"
          spellCheck={false}
        />
      </div>
    </ActionDialog>
  )
}
