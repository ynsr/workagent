import { useMemo, useState } from "react"
import { Play, Workflow } from "lucide-react"
import { toast } from "sonner"
import { useNavigate } from "react-router-dom"
import { ActionDialog } from "@/components/LinkDialogs"
import { RunModeSelect } from "@/components/StartForm"
import { SearchableSelect } from "@/components/SearchableSelect"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { errorText } from "@/components/StatusFeedback"
import { useConfirm } from "@/lib/confirm"
import { useActions, useCreateRun, useFlowTemplates, useRepos } from "@/lib/queries"
import { buildMessageArgs } from "@/lib/messageLaunchConfig"
import {
  ROW_CATEGORIES,
  buildImmediateActionArgs,
  buildImmediateFlowArgs,
  buildImmediateWorktreeFlowArgs,
  isRunImmediateAction,
  isRunImmediateTemplate,
  messageScoped,
  splitRefs,
  type RowCategory,
} from "@/lib/messageRunConfig"
import type { RunMode } from "@/lib/launchConfig"

export type ImmediateRunKind = "action" | "flow"

export interface ImmediateRunFormProps {
  kind: ImmediateRunKind
  /** Row category the run is scoped to (actions/templates are per category). */
  category?: RowCategory
  /** Preselected label (row Run buttons). */
  preset?: string
  /** Prefilled message id (message rows / message scope). */
  messageRef?: string
  /** Prefilled worktree refs (dashboard selection / worktree scope). */
  worktreeRefs?: string[]
  /** Fix the category (row buttons know their row's scope; page cards do not). */
  lockCategory?: boolean
  /** Called after at least one run launched. */
  onDone?: () => void
}

/** Shared immediate Run Action / Run Flow form: pick the row (filtered by
 * category), pick its scope ref(s), then launch with the standard confirm.
 * Used by the Actions / Flow Templates page cards, their row Run buttons,
 * the Messages row Run buttons, and the Dashboard menus. */
export function ImmediateRunForm({
  kind,
  category: initialCategory = "worktree",
  preset = "",
  messageRef = "",
  worktreeRefs = [],
  lockCategory = false,
  onDone,
}: ImmediateRunFormProps) {
  const navigate = useNavigate()
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const { data: actionsData } = useActions()
  const { data: templatesData } = useFlowTemplates()
  const [category, setCategory] = useState<RowCategory>(initialCategory)
  const [label, setLabel] = useState(preset)
  const [message, setMessage] = useState(messageRef)
  const [worktree, setWorktree] = useState(worktreeRefs.join(", "))
  const [refOverride, setRefOverride] = useState("")
  const [repo, setRepo] = useState("")
  const [runMode, setRunMode] = useState<RunMode>("headless")
  const [running, setRunning] = useState(false)

  const rows = useMemo(() => {
    if (kind === "action") {
      return messageScoped(actionsData?.actions ?? [], category).map((r) => ({ label: r.label, name: r.name }))
    }
    return messageScoped(templatesData?.templates ?? [], category).map((r) => ({ label: r.label, name: r.name }))
  }, [kind, category, actionsData, templatesData])

  const refs = splitRefs(worktree)
  const valid = label.trim().length > 0 && (category === "message"
    ? message.trim().length > 0 && (kind === "action" || refOverride.trim().length > 0)
    : refs.length > 0)

  function argsFor(ref: string): { command: "action" | "flow" | "message"; args: string[] } {
    const lbl = label.trim()
    if (category === "message") {
      if (kind === "action") {
        return {
          command: "message",
          args: buildMessageArgs("action", {
            ref: message.trim(), label: lbl, runMode, refOverride, repo,
          }),
        }
      }
      return { command: "message", args: buildImmediateFlowArgs(lbl, message.trim(), refOverride) }
    }
    if (kind === "action") {
      return { command: "action", args: buildImmediateActionArgs(lbl, ref, runMode) }
    }
    return { command: "flow", args: buildImmediateWorktreeFlowArgs(lbl, ref) }
  }

  async function handleRun() {
    if (!valid || running) return
    const targets = category === "message" ? [message.trim()] : refs
    const ok = await confirm({
      action: kind === "flow" ? "flow" : null,
      title: `Run ${kind === "action" ? "action" : "flow"} ${label.trim()}`,
      description: kind === "action"
        ? "The agent runs now with auto-approve. The server appends --yes."
        : "Runs the flow template now. The server appends --yes.",
      confirmLabel: targets.length > 1 ? `Run ${targets.length}` : "Run now",
      details: [
        { label: kind === "action" ? "Action" : "Template", value: label.trim(), mono: true },
        { label: "Scope", value: targets.join(", "), mono: true },
        ...(category === "message" && refOverride.trim()
          ? [{ label: "Worktree", value: refOverride.trim(), mono: true }]
          : []),
        ...(category === "message" && repo.trim()
          ? [{ label: "Repo", value: repo.trim(), mono: true }]
          : []),
      ],
    })
    if (!ok) return
    setRunning(true)
    try {
      const jobArgSets: { command: "action" | "flow" | "message"; args: string[] }[] =
        targets.map((ref) => argsFor(ref))
      for (const { command, args } of jobArgSets) {
        try {
          const { run_id } = await createRun.mutateAsync({ command, args, confirm: true })
          toast.success(`${kind === "action" ? "Action" : "Flow"} ${label.trim()} on ${message.trim() || targets.join(", ")} started`, {
            action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
          })
        } catch (err) {
          toast.error(`${message.trim() || targets.join(", ")}: ${errorText(err)}`)
        }
      }
      onDone?.()
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="grid gap-3">
      {!lockCategory ? (
        <div className="grid gap-2">
          <Label htmlFor="immediate-run-category">Category</Label>
          <select
            id="immediate-run-category"
            className="h-9 rounded-md border bg-background px-2 text-sm"
            value={category}
            onChange={(e) => {
              setCategory(e.target.value as RowCategory)
              setLabel("")
            }}
          >
            {ROW_CATEGORIES.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </div>
      ) : null}
      <div className="grid gap-2">
        <Label htmlFor="immediate-run-label">{kind === "action" ? "Action" : "Flow template"}</Label>
        <select
          id="immediate-run-label"
          className="h-9 rounded-md border bg-background px-2 text-sm"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        >
          <option value="">{kind === "action" ? "Run action…" : "Run flow…"}</option>
          {rows.map((r) => (
            <option key={r.label} value={r.label}>{r.name}</option>
          ))}
        </select>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No {category}-category {kind === "action" ? "actions" : "flow templates"} registered.</p>
        ) : null}
      </div>
      {category === "message" ? (
        <>
          <div className="grid gap-2">
            <Label htmlFor="immediate-run-message">Message ref</Label>
            <Input
              id="immediate-run-message"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="message_id, thread, or permalink"
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="immediate-run-worktree">
              {kind === "flow" ? "Worktree (required)" : "Worktree override"}
            </Label>
            <Input
              id="immediate-run-worktree"
              value={refOverride}
              onChange={(e) => setRefOverride(e.target.value)}
              placeholder={kind === "flow" ? "Linked worktree ref" : "Optional linked worktree ref"}
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <RepoPinSelect value={repo} onChange={setRepo} />
        </>
      ) : (
        <div className="grid gap-2">
          <Label htmlFor="immediate-run-refs">Worktrees</Label>
          <Input
            id="immediate-run-refs"
            value={worktree}
            onChange={(e) => setWorktree(e.target.value)}
            placeholder="worktree refs, comma or space separated"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
      )}
      {kind === "action" ? (
        <RunModeSelect idPrefix="immediate-run" value={runMode} onChange={setRunMode} />
      ) : null}
      <Button type="button" disabled={!valid || running} onClick={() => void handleRun()}>
        {kind === "action" ? <Play aria-hidden /> : <Workflow aria-hidden />}
        {running ? "Launching…" : `Run ${kind} now`}
      </Button>
    </div>
  )
}

/** Single repo pin for the message branch (`--repo` — prompt context only). */
function RepoPinSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { data: repos } = useRepos()
  const options = useMemo(() => (repos ?? []).map((r) => r.name).sort(), [repos])
  return (
    <div className="grid gap-2">
      <Label htmlFor="immediate-run-repo">Repo pin</Label>
      <SearchableSelect
        id="immediate-run-repo"
        value={value}
        options={options}
        onChange={onChange}
        placeholder="Optional: registered repo name"
        allowCustom
      />
    </div>
  )
}

/** Modal wrapper: row Run buttons open this with the row's label and scope
 * prefilled, so every surface shares the same form. */
export function ImmediateRunDialog(props: ImmediateRunFormProps & { onClose: () => void }) {
  const { onClose, ...form } = props
  return (
    <ActionDialog
      title={`Run ${form.kind === "action" ? "action" : "flow"} now`}
      description="Launches immediately with the standard confirm."
      onClose={onClose}
      footer={<Button size="sm" variant="outline" onClick={onClose}>Close</Button>}
    >
      <ImmediateRunForm {...form} onDone={() => { form.onDone?.(); onClose() }} />
    </ActionDialog>
  )
}

/** Gate for row/menu Run buttons: rows opted into run_immediately launch
 * straight away (standard confirm); everything else opens the shared form. */
export function runsImmediately(kind: ImmediateRunKind, row: unknown): boolean {
  if (kind === "action") return isRunImmediateAction(row as { run_immediately?: unknown })
  return isRunImmediateTemplate(row as { run_immediately?: unknown })
}
