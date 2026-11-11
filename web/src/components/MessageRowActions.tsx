import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { ClipboardList, FlaskConical, Play, Trash2, Zap } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ImmediateRunDialog, runsImmediately } from "@/components/ImmediateRunForm"
import { errorText } from "@/components/StatusFeedback"
import { useActions, useCreateRun, useFlowTemplates } from "@/lib/queries"
import { useConfirm } from "@/lib/confirm"
import { VALIDATE_CLAIMED_ISSUES_PROMPT } from "@/lib/messageLaunchConfig"
import { buildImmediateMessageActionArgs, messageScoped } from "@/lib/messageRunConfig"
import type { ActionRow, FlowTemplateRow, MessageRow } from "@/lib/api"

/** Row overlay: Adhoc / Validate / Run action / Run flow / Delete.
 * Run buttons open the shared immediate run form first — seeded with the
 * row's label and message — unless the row opted into run_immediately. */
export function MessageRowActions({ row, onDone }: { row: MessageRow; onDone?: () => void }) {
  const navigate = useNavigate()
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const { data: actionsData } = useActions()
  const { data: templatesData } = useFlowTemplates()
  const actions: ActionRow[] = messageScoped(actionsData?.actions ?? [], "message")
  const templates: FlowTemplateRow[] = messageScoped(templatesData?.templates ?? [], "message")
  const [prompt, setPrompt] = useState("")
  const [actionLabel, setActionLabel] = useState("")
  const [templateLabel, setTemplateLabel] = useState("")
  const [runForm, setRunForm] = useState<{ kind: "action" | "flow"; label: string } | null>(null)

  function handleRunClick(kind: "action" | "flow", label: string) {
    // Immediate actions launch straight away (standard confirm; the message
    // itself is the ref). Flows always need a worktree ref, so they go
    // through the shared run form first.
    if (kind === "action" && runsImmediately(kind, actions.find((a) => a.label === label))) {
      void spawn(
        buildImmediateMessageActionArgs(label, row.message_id),
        true,
        "Message action started",
      )
      return
    }
    setRunForm({ kind, label })
  }

  async function spawn(args: string[], needsConfirm: boolean, okMsg: string) {
    try {
      const { run_id } = await createRun.mutateAsync({ command: "message", args, confirm: needsConfirm })
      toast.success(okMsg, { action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) } })
      onDone?.()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function handleDelete() {
    const ok = await confirm({ title: `Delete message ${row.message_id}?`, confirmLabel: "Delete" })
    if (!ok) return
    await spawn(["remove", row.message_id, "--yes"], true, "Message delete started")
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="flex items-center gap-1">
        <Input className="h-8 w-44" placeholder="Adhoc prompt…" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        <Button variant="outline" size="sm" disabled={!prompt.trim()}
          onClick={() => void spawn(["adhoc", row.message_id, "--prompt", prompt.trim(), "--yes"], true, "Message adhoc started")}>
          <Play aria-hidden /> Adhoc
        </Button>
        <Button variant="outline" size="sm"
          onClick={() => void spawn(["validate", row.message_id, "--prompt", VALIDATE_CLAIMED_ISSUES_PROMPT, "--yes"], true, "Message validate started")}>
          <FlaskConical aria-hidden /> Validate
        </Button>
      </div>
      {actions.length > 0 && (
        <div className="flex items-center gap-1">
          <Label className="sr-only">Action</Label>
          <select className="h-8 rounded-md border bg-background px-2 text-sm" value={actionLabel}
            onChange={(e) => setActionLabel(e.target.value)} aria-label="Message action">
            <option value="">Run action…</option>
            {actions.map((a) => <option key={a.label} value={a.label}>{a.name}</option>)}
          </select>
          <Button variant="outline" size="sm" disabled={!actionLabel}
            onClick={() => handleRunClick("action", actionLabel)}>
            <Zap aria-hidden /> Run
          </Button>
        </div>
      )}
      {templates.length > 0 && (
        <div className="flex items-center gap-1">
          <Label className="sr-only">Flow</Label>
          <select className="h-8 rounded-md border bg-background px-2 text-sm" value={templateLabel}
            onChange={(e) => setTemplateLabel(e.target.value)} aria-label="Message flow">
            <option value="">Run flow…</option>
            {templates.map((t) => <option key={t.label} value={t.label}>{t.name}</option>)}
          </select>
          <Button variant="outline" size="sm" disabled={!templateLabel}
            onClick={() => handleRunClick("flow", templateLabel)}>
            <ClipboardList aria-hidden /> Run
          </Button>
        </div>
      )}
      <Button variant="outline" size="sm" onClick={() => void handleDelete()}>
        <Trash2 aria-hidden /> Delete
      </Button>
      {runForm ? (
        <ImmediateRunDialog kind={runForm.kind} category="message" lockCategory preset={runForm.label} messageRef={row.message_id} onClose={() => setRunForm(null)} />
      ) : null}
    </div>
  )
}
