import { useMemo, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import { MessageSquarePlus, Rocket } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { RunModeSelect } from "@/components/StartForm"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { CheckRow, FieldHelp } from "@/components/FieldHelp"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useConfirm } from "@/lib/confirm"
import { errorText } from "@/components/StatusFeedback"
import { useActions, useCreateRun, useFlowTemplates, useMessages } from "@/lib/queries"
import { cn } from "@/lib/utils"
import {
  MESSAGE_LAUNCH_INITIAL,
  MESSAGE_LAUNCH_LABELS,
  MESSAGE_LAUNCH_MODES,
  buildMessageArgs,
  messageLaunchValid,
  type MessageLaunchForm,
  type MessageLaunchMode,
} from "@/lib/messageLaunchConfig"
import type { RunMode } from "@/lib/launchConfig"

function messageScoped<T extends { category?: string }>(rows: T[]): T[] {
  return rows.filter((r) => (r.category ?? "worktree") === "message")
}

const CONFIRM_COPY: Record<MessageLaunchMode, { title: string; description: string }> = {
  adhoc: {
    title: "Launch message adhoc",
    description: "The agent runs now with auto-approve — headless (--launch) or in an OS terminal (--terminal). The server appends --yes.",
  },
  validate: {
    title: "Launch message validate",
    description: "The agent validates the message claims against its linked repos with auto-approve. The server appends --yes.",
  },
  "draft-reply": {
    title: "Save message draft reply",
    description: "Saves a draft reply via mm (never direct-send).",
  },
  action: {
    title: "Run message action",
    description: "Runs a message-category custom action grounded in this message. The server appends --yes.",
  },
  flow: {
    title: "Run message flow",
    description: "Runs a message-category flow template grounded in this message. The server appends --yes.",
  },
}
const NEEDS_RUN_MODE: Record<MessageLaunchMode, boolean> = {
  adhoc: true,
  validate: true,
  "draft-reply": false,
  action: true,
  flow: false,
}


export function MessageLaunch() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const { data: messagesData } = useMessages()
  const { data: actionsData } = useActions()
  const { data: templatesData } = useFlowTemplates()
  const refParam = params.get("ref") ?? ""

  const [tab, setTab] = useState<MessageLaunchMode>("adhoc")
  const [form, setForm] = useState<MessageLaunchForm>(() => ({
    ...MESSAGE_LAUNCH_INITIAL,
    ref: refParam,
  }))
  const [submitting, setSubmitting] = useState(false)

  const actions = useMemo(
    () => messageScoped(actionsData?.actions ?? []),
    [actionsData],
  )
  const templates = useMemo(
    () => messageScoped(templatesData?.templates ?? []),
    [templatesData],
  )
  const messageIds = useMemo(
    () => (messagesData?.messages ?? []).map((m) => m.message_id),
    [messagesData],
  )

  function update<K extends keyof MessageLaunchForm>(key: K, value: MessageLaunchForm[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  const valid = messageLaunchValid(tab, form)
  const args = buildMessageArgs(tab, form)

  async function handleSubmit() {
    if (!valid || submitting) return
    if (tab === "action" && !form.label) {
      toast.error("Pick a message action — none available.")
      return
    }
    if (tab === "flow" && !form.label) {
      toast.error("Pick a message flow template — none available.")
      return
    }
    const copy = CONFIRM_COPY[tab]
    const ok = await confirm({
      title: copy.title,
      description: copy.description,
      confirmLabel: tab === "draft-reply" ? "Save draft" : "Launch",
      details: [
        { label: "Message", value: form.ref.trim(), mono: true },
        ...(tab === "adhoc" && form.prompt.trim() ? [{ label: "Prompt", value: form.prompt.trim().slice(0, 200) }] : []),
        ...(tab === "validate" && form.prompt.trim() ? [{ label: "Instructions", value: form.prompt.trim().slice(0, 200) }] : []),
        ...(tab === "draft-reply" ? [{ label: "Text", value: form.text.trim().slice(0, 200) }] : []),
        ...((tab === "action" || tab === "flow") && form.label ? [{ label: tab === "action" ? "Action" : "Template", value: form.label, mono: true }] : []),
        ...(form.refOverride.trim() ? [{ label: "Worktree", value: form.refOverride.trim(), mono: true }] : []),
        ...(form.repo.trim() ? [{ label: "Repo", value: form.repo.trim(), mono: true }] : []),
        { label: "Flags", value: args.slice(2).join(" ") || "(none)" },
      ],
    })
    if (!ok) return
    setSubmitting(true)
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "message",
        args,
        confirm: true,
      })
      toast.success(`${MESSAGE_LAUNCH_LABELS[tab]} started`, {
        action: { label: "View", onClick: () => navigate(`/runs/${run_id}`) },
      })
      navigate(`/runs/${run_id}`)
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div>
      <PageHeader
        title="Message Launch"
        description="Run an agent grounded in a registered chat message — adhoc, validate, draft a reply, or run a message action/flow. Runs headless as a child process of workagent serve."
        actions={null}
      />
      <Card className="mx-auto max-w-2xl">
        <CardHeader>
          <div>
            <CardTitle>{MESSAGE_LAUNCH_LABELS[tab]}</CardTitle>
            <CardDescription>
              Message id, message_id, or thread — resolvable via `message resolve`.
            </CardDescription>
          </div>
          <div
            role="tablist"
            aria-label="Message launch mode"
            className="mt-3 inline-flex max-w-full flex-wrap rounded-lg border p-1"
          >
            {MESSAGE_LAUNCH_MODES.map((m) => (
              <button
                key={m}
                role="tab"
                aria-selected={tab === m}
                onClick={() => setTab(m)}
                className={cn(
                  "min-h-9 shrink-0 rounded-md px-4 text-sm font-medium transition-colors",
                  tab === m
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {MESSAGE_LAUNCH_LABELS[m]}
              </button>
            ))}
          </div>
        </CardHeader>
        <CardContent className="grid min-h-[420px] content-start gap-5">
          <div className="grid gap-2">
            <Label htmlFor="message-launch-ref">Message ref</Label>
            <Input
              id="message-launch-ref"
              value={form.ref}
              onChange={(e) => update("ref", e.target.value)}
              placeholder="message_id or thread id"
              autoComplete="off"
              spellCheck={false}
              list="message-launch-ids"
            />
            <datalist id="message-launch-ids">
              {messageIds.map((id) => (
                <option key={id} value={id} />
              ))}
            </datalist>
          </div>

          {tab === "adhoc" ? (
            <div className="grid gap-2">
              <Label htmlFor="message-launch-prompt">
                <FieldHelp label="Prompt" flag="--prompt" description="Instructions for the agent, grounded in the message (required)." />
              </Label>
              <textarea
                id="message-launch-prompt"
                value={form.prompt}
                onChange={(e) => update("prompt", e.target.value)}
                placeholder="e.g. summarize what this message asks for"
                autoComplete="off"
                spellCheck={false}
                rows={4}
                className="min-h-24 w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
              />
            </div>
          ) : null}

          {tab === "validate" ? (
            <div className="grid gap-2">
              <Label htmlFor="message-launch-validate-prompt">
                <FieldHelp label="Extra instructions" flag="--prompt" description="Appended to the fixed CONFIRMED/REFUTED validation prompt (optional)." />
              </Label>
              <textarea
                id="message-launch-validate-prompt"
                value={form.prompt}
                onChange={(e) => update("prompt", e.target.value)}
                placeholder="Optional extra validation instructions"
                autoComplete="off"
                spellCheck={false}
                rows={3}
                className="min-h-20 w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
              />
            </div>
          ) : null}

          {tab === "draft-reply" ? (
            <div className="grid gap-2">
              <Label htmlFor="message-launch-text">
                <FieldHelp label="Draft body" flag="--text" description="Draft text saved via mm (never direct-send; required)." />
              </Label>
              <textarea
                id="message-launch-text"
                value={form.text}
                onChange={(e) => update("text", e.target.value)}
                placeholder="e.g. acknowledged, looking into it"
                autoComplete="off"
                spellCheck={false}
                rows={4}
                className="min-h-24 w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
              />
            </div>
          ) : null}

          {tab === "action" ? (
            <div className="grid gap-2">
              <Label htmlFor="message-launch-action">
                <FieldHelp label="Action" flag="action run <label>" description="Message-category custom action to run." />
              </Label>
              <select
                id="message-launch-action"
                className="h-9 rounded-md border bg-background px-2 text-sm"
                value={form.label}
                onChange={(e) => update("label", e.target.value)}
                aria-label="Message action"
              >
                <option value="">Run action…</option>
                {actions.map((a) => (
                  <option key={a.label} value={a.label}>{a.name}</option>
                ))}
              </select>
              {actions.length === 0 ? (
                <p className="text-sm text-muted-foreground">No message-category actions registered.</p>
              ) : null}
            </div>
          ) : null}

          {tab === "flow" ? (
            <div className="grid gap-2">
              <Label htmlFor="message-launch-flow">
                <FieldHelp label="Flow template" flag="flow run <label>" description="Message-category flow template to run." />
              </Label>
              <select
                id="message-launch-flow"
                className="h-9 rounded-md border bg-background px-2 text-sm"
                value={form.label}
                onChange={(e) => update("label", e.target.value)}
                aria-label="Message flow"
              >
                <option value="">Run flow…</option>
                {templates.map((t) => (
                  <option key={t.label} value={t.label}>{t.name}</option>
                ))}
              </select>
              {templates.length === 0 ? (
                <p className="text-sm text-muted-foreground">No message-category flow templates registered.</p>
              ) : null}
            </div>
          ) : null}

          {tab === "draft-reply" ? null : (
            <>
          <div className="grid gap-2">
            <Label htmlFor="message-launch-worktree">
              <FieldHelp label={tab === "flow" ? "Worktree (required)" : "Worktree override"} flag="--ref" description={tab === "flow" ? "Worktree ref the flow runs on (required — a message alone is not a resolvable flow ref)." : "Worktree ref that wins over the message repos[] (optional)."} />
            </Label>
            <Input
              id="message-launch-worktree"
              value={form.refOverride}
              onChange={(e) => update("refOverride", e.target.value)}
              placeholder="jira:IPG-932@proj"
              autoComplete="off"
              spellCheck={false}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="message-launch-repo">
              <FieldHelp label="Repo pin" flag="--repo" description="Pin one repo for multi-repo messages (optional)." />
            </Label>
            <Input
              id="message-launch-repo"
              value={form.repo}
              onChange={(e) => update("repo", e.target.value)}
              placeholder="Registered repo name"
              autoComplete="off"
              spellCheck={false}
            />
          </div>
            </>
          )}

          <div className="flex flex-wrap gap-x-6 gap-y-3">
            {NEEDS_RUN_MODE[tab] ? (
              <RunModeSelect
                idPrefix="message-launch"
                value={form.runMode}
                onChange={(v: RunMode) => update("runMode", v)}
              />
            ) : null}
            {tab === "draft-reply" ? (
              <CheckRow
                id="message-launch-dry-run"
                checked={form.dryRun}
                onChange={(v) => update("dryRun", v)}
                label="Dry run"
                flag="--dry-run"
                description="Print the mm draft argv without calling mm."
              />
            ) : null}
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              type="button"
              onClick={() => void handleSubmit()}
              disabled={!valid || submitting}
            >
              {tab === "draft-reply" ? (
                <MessageSquarePlus aria-hidden />
              ) : (
                <Rocket aria-hidden />
              )}
              {submitting
                ? "Launching…"
                : tab === "draft-reply"
                  ? "Save draft"
                  : `Launch ${MESSAGE_LAUNCH_LABELS[tab]}`}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
