import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { ClipboardList, FlaskConical, MessageSquarePlus, Play, Trash2, Zap } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { errorText } from "@/components/StatusFeedback"
import { useActions, useCreateRun, useFlowTemplates } from "@/lib/queries"
import { useConfirm } from "@/lib/confirm"
import type { ActionRow, FlowTemplateRow, MessageRow } from "@/lib/api"

function messageScoped<T extends { category?: string }>(rows: T[]): T[] {
  return rows.filter((r) => (r.category ?? "worktree") === "message")
}

/** Row overlay: Adhoc / Validate / Draft reply / Run action / Run flow / Delete. */
export function MessageRowActions({ row, onDone }: { row: MessageRow; onDone?: () => void }) {
  const navigate = useNavigate()
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const { data: actionsData } = useActions()
  const { data: templatesData } = useFlowTemplates()
  const actions: ActionRow[] = messageScoped(actionsData?.actions ?? [])
  const templates: FlowTemplateRow[] = messageScoped(templatesData?.templates ?? [])
  const [prompt, setPrompt] = useState("")
  const [text, setText] = useState("")
  const [actionLabel, setActionLabel] = useState("")
  const [templateLabel, setTemplateLabel] = useState("")

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
    await spawn(["remove", row.message_id, "--yes"], false, "Message delete started")
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
          onClick={() => void spawn(["validate", row.message_id, "--yes"], true, "Message validate started")}>
          <FlaskConical aria-hidden /> Validate
        </Button>
      </div>
      <div className="flex items-center gap-1">
        <Input className="h-8 w-44" placeholder="Draft reply…" value={text} onChange={(e) => setText(e.target.value)} />
        <Button variant="outline" size="sm" disabled={!text.trim()}
          onClick={() => void spawn(["draft-reply", row.message_id, "--text", text.trim()], false, "Draft reply saved")}>
          <MessageSquarePlus aria-hidden /> Draft reply
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
            onClick={() => void spawn(["action", "run", actionLabel, row.message_id, "--dry-run"], false, "Message action planned")}>
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
            onClick={() => void spawn(["flow", "run", templateLabel, row.message_id, "--dry-run"], false, "Message flow planned")}>
            <ClipboardList aria-hidden /> Run
          </Button>
        </div>
      )}
      <Button variant="outline" size="sm" onClick={() => void handleDelete()}>
        <Trash2 aria-hidden /> Delete
      </Button>
    </div>
  )
}
