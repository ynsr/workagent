import { useMemo, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import { Rocket } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { RunModeSelect } from "@/components/StartForm"
import { SearchableSelect } from "@/components/SearchableSelect"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { FieldHelp } from "@/components/FieldHelp"
import { Label } from "@/components/ui/label"
import { useConfirm } from "@/lib/confirm"
import { errorText } from "@/components/StatusFeedback"
import { useActions, useCreateRun, useFlowTemplates, useMessages, useRepos, useStatusAll } from "@/lib/queries"
import { cn } from "@/lib/utils"
import {
  MESSAGE_LAUNCH_INITIAL,
  MESSAGE_LAUNCH_LABELS,
  MESSAGE_LAUNCH_MODES,
  VALIDATE_CLAIMED_ISSUES_PROMPT,
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
    title: "Launch validate claimed issues",
    description: "The agent validates the claimed issues and generates Jira issues if confirmed. The server appends --yes.",
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
  const { data: reposData } = useRepos()
  const { data: statusData } = useStatusAll()
  const refParam = params.get("ref") ?? ""

  const [tab, setTab] = useState<MessageLaunchMode>("adhoc")
  const [form, setForm] = useState<MessageLaunchForm>(() => ({
    ...MESSAGE_LAUNCH_INITIAL,
    ref: refParam,
    prompt: refParam ? "" : MESSAGE_LAUNCH_INITIAL.prompt,
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
  const messageOptions = useMemo(
    () => (messagesData?.messages ?? []).map((m) => m.message_id),
    [messagesData],
  )
  const worktreeOptions = useMemo(
    () => Object.keys(statusData ?? {}).sort(),
    [statusData],
  )
  const repoOptions = useMemo(
    () => (reposData ?? []).map((r) => r.name).sort(),
    [reposData],
  )

  function handleTab(next: MessageLaunchMode) {
    setTab(next)
    // Seed the validate prompt with the claimed-issues copy on first visit.
    if (next === "validate") {
      setForm((f) => (f.prompt.trim() ? f : { ...f, prompt: VALIDATE_CLAIMED_ISSUES_PROMPT }))
    }
  }

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
      confirmLabel: "Launch",
      details: [
        { label: "Message", value: form.ref.trim(), mono: true },
        ...(tab === "adhoc" && form.prompt.trim() ? [{ label: "Prompt", value: form.prompt.trim().slice(0, 200) }] : []),
        ...(tab === "validate" && form.prompt.trim() ? [{ label: "Instructions", value: form.prompt.trim().slice(0, 200) }] : []),
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
        description="Run an agent grounded in a registered chat message — adhoc, validate claimed issues, or run a message action/flow. Runs headless as a child process of workagent serve."
        actions={null}
      />
      <Card className="mx-auto max-w-2xl">
        <CardHeader>
          <div>
            <CardTitle>{MESSAGE_LAUNCH_LABELS[tab]}</CardTitle>
            <CardDescription>
              Message id, message_id, thread, or Mattermost permalink — resolvable via `message resolve`.
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
                onClick={() => handleTab(m)}
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
            <SearchableSelect
              id="message-launch-ref"
              value={form.ref}
              options={messageOptions}
              onChange={(v) => update("ref", v)}
              placeholder="message_id, thread, or Mattermost permalink"
              allowCustom
            />
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
                <FieldHelp label="Instructions" flag="--prompt" description="Validation prompt — seeded to confirm issues and file Jira issues (required)." />
              </Label>
              <textarea
                id="message-launch-validate-prompt"
                value={form.prompt}
                onChange={(e) => update("prompt", e.target.value)}
                placeholder={VALIDATE_CLAIMED_ISSUES_PROMPT}
                autoComplete="off"
                spellCheck={false}
                rows={3}
                className="min-h-20 w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
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

          <div className="grid gap-2">
            <Label htmlFor="message-launch-worktree">
              <FieldHelp label={tab === "flow" ? "Worktree (required)" : "Worktree override"} flag="--ref" description={tab === "flow" ? "Worktree ref the flow runs on (required — a message alone is not a resolvable flow ref)." : "Worktree ref that wins over the message repos[] (optional)."} />
            </Label>
            <SearchableSelect
              id="message-launch-worktree"
              value={form.refOverride}
              options={worktreeOptions}
              onChange={(v) => update("refOverride", v)}
              placeholder={tab === "flow" ? "Required: linked worktree ref" : "Optional: linked worktree ref"}
              allowCustom
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="message-launch-repo">
              <FieldHelp label="Repo pin" flag="--repo" description="Pin one repo for multi-repo messages (optional)." />
            </Label>
            <SearchableSelect
              id="message-launch-repo"
              value={form.repo}
              options={repoOptions}
              onChange={(v) => update("repo", v)}
              placeholder="Optional: registered repo name"
              allowCustom
            />
          </div>

          <div className="flex flex-wrap gap-x-6 gap-y-3">
            {NEEDS_RUN_MODE[tab] ? (
              <RunModeSelect
                idPrefix="message-launch"
                value={form.runMode}
                onChange={(v: RunMode) => update("runMode", v)}
              />
            ) : null}
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              type="button"
              onClick={() => void handleSubmit()}
              disabled={!valid || submitting}
            >
              <Rocket aria-hidden />
              {submitting ? "Launching…" : `Launch ${MESSAGE_LAUNCH_LABELS[tab]}`}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
