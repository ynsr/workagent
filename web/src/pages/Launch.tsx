import { useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { RefreshCw, Rocket, ShieldAlert, Workflow } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { FlowWizard } from "@/components/FlowWizard"
import { ActionDialog } from "@/components/LinkDialogs"
import { SearchableSelect } from "@/components/SearchableSelect"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useConfirm } from "@/lib/confirm"
import { errorText } from "@/components/StatusFeedback"
import { queryKeys, useCreateRun, useFlowTemplates, useLinks, useRepos } from "@/lib/queries"
import { api, type FlowTemplateRow } from "@/lib/api"
import { cn } from "@/lib/utils"

import { CheckRow, FieldHelp } from "@/components/FieldHelp"
import { ReviewActionSelect, RunModeSelect, StartFormFields, buildStartArgs } from "@/components/StartForm"
import { COPY, INITIAL, MODES, extraPromptArgs, isPrShaped, runModeArgs } from "@/lib/launchConfig"
import type { LaunchForm, Mode } from "@/lib/launchConfig"
import { loadLaunchPrefs, saveLaunchPrefs } from "@/lib/launchPrefs"
import type { LaunchPrefs } from "@/lib/launchPrefs"

/** Template picker for the Launch page: picks a flow template, then the
 * shared FlowWizard scopes it to the issue ref input. */
function TemplatePickerDialog({
  onClose,
  onPick,
}: {
  onClose: () => void
  onPick: (t: FlowTemplateRow) => void
}) {
  const { data } = useFlowTemplates()
  const templates = data?.templates ?? []
  return (
    <ActionDialog
      title="Run a flow template"
      description="Pick a template — the wizard scopes it to the issue ref above."
      onClose={onClose}
      footer={<></>}
    >
      {templates.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No flow templates yet.{" "}
          <a href="/flow-templates" className="underline underline-offset-2">Add one</a>.
        </p>
      ) : (
        <div className="grid gap-1">
          {templates.map((t) => (
            <button
              key={t.label}
              type="button"
              onClick={() => onPick(t)}
              className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm hover:bg-accent"
            >
              <Workflow aria-hidden className="size-4 shrink-0" />
              <span className="min-w-0 flex-1 truncate">{t.name}</span>
              <span className="shrink-0 font-mono text-xs text-muted-foreground">
                {t.steps?.length ?? 0} steps
              </span>
            </button>
          ))}
        </div>
      )}
    </ActionDialog>
  )
}
/** Launch prefs slice for the current mode: hydrates run-mode toggles, the
 * start base, and the review fix flags. `ref`/`repo`/free text are never
 * persisted, and deep-link URL params win over stored values on mount. */
function prefsForMode(p: LaunchPrefs, mode: Mode): Partial<LaunchForm> {
  if (mode === "sync") return { merge: p.sync.merge }
  if (mode === "review") {
    return {
      runMode: p.review.runMode as LaunchForm["runMode"],
      reviewAction: p.review.fixAction as LaunchForm["reviewAction"],
      fixComments: p.review.fixAction === "fix",
      thenFixComments: p.review.thenFix,
      useReviewSession: p.review.useReviewSession,
    }
  }
  return { runMode: p.start.runMode as LaunchForm["runMode"], base: p.start.base }
}

/** Review/Sync fields (kept on the Launch page; Start lives in StartForm). */
function ExtraPromptField({ form, onChange, id = "launch-extra-prompt" }: {
  form: LaunchForm
  onChange: <K extends keyof LaunchForm>(key: K, value: LaunchForm[K]) => void
  id?: string
}) {
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>
        <FieldHelp label="Extra prompt" flag="--extra-prompt" description="Extra instructions appended to the agent prompt." />
      </Label>
      <Input
        id={id}
        value={form.extraPrompt}
        onChange={(e) => onChange("extraPrompt", e.target.value)}
        placeholder="e.g. keep the diff minimal"
        autoComplete="off"
        spellCheck={false}
      />
    </div>
  )
}

/** Review/Sync fields (kept on the Launch page; Start lives in StartForm). */
function LaunchOtherFields({ mode, form, onChange, copy, repoNames }: {
  mode: Exclude<Mode, "flow">
  form: LaunchForm
  onChange: <K extends keyof LaunchForm>(key: K, value: LaunchForm[K]) => void
  refParam: string
  issuesNonce: number
  copy: (typeof COPY)[Mode]
  repoNames: string[]
}) {
  if (mode === "sync") {
    return (
      <CheckRow
        id="launch-merge"
        checked={form.merge}
        onChange={(v) => onChange("merge", v)}
        label="Merge locally"
        flag="--merge"
        description="Merge locally instead of the remote rebase."
      />
    )
  }
  return (
    <>
      <div className="grid gap-2">
        <Label htmlFor="launch-ref">PR/MR ref</Label>
        <Input
          id="launch-ref"
          value={form.ref}
          onChange={(e) => onChange("ref", e.target.value)}
          placeholder={copy.refPlaceholder}
          autoComplete="off"
          spellCheck={false}
        />
      </div>
      <div className="grid gap-2">
        <Label htmlFor="launch-repo">
          <FieldHelp label="Repository" flag="--repo" description="Registered name, local path, or clone URL. Required — prefilled from the ref when the backend knows it." />
        </Label>
        <SearchableSelect
          value={form.repo}
          options={repoNames}
          onChange={(v) => onChange("repo", v)}
          placeholder="Select a repo…"
          allowCustom
        />
      </div>
      <ExtraPromptField form={form} onChange={onChange} />
    </>
  )
}

/** Flow tab: template picker (start-first templates only) + issue ref.
 * Submit opens the shared FlowWizard scoped to the ref. */
function FlowTabFields({
  issuesNonce,
  initialTemplate,
}: {
  issuesNonce?: number
  initialTemplate?: string
}) {
  const { data } = useFlowTemplates()
  const templates = (data?.templates ?? []).filter(
    (t) => (t.steps ?? [])[0]?.kind === "start",
  )
  const [templateLabel, setTemplateLabel] = useState(initialTemplate ?? "")
  const [ref, setRef] = useState("")
  const [wizard, setWizard] = useState<FlowTemplateRow | null>(null)
  const template = templates.find((t) => t.label === templateLabel) ?? null
  const refValue = ref.trim()
  return (
    <>
      <div className="grid gap-2">
        <Label htmlFor="launch-flow-template">Flow template</Label>
        {templates.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No start-first flow templates yet.{" "}
            <a href="/flow-templates" className="underline underline-offset-2">Add one</a>.
          </p>
        ) : (
          <SearchableSelect
            id="launch-flow-template"
            value={template ? `${template.name} (${template.label})` : ""}
            options={templates.map((t) => `${t.name} (${t.label})`)}
            onChange={(v) => {
              const m = v.match(/\(([^)]+)\)\s*$/)
              setTemplateLabel(m?.[1] ?? "")
            }}
            placeholder="Select a flow template…"
          />
        )}
      </div>
      <div className="grid gap-2">
        <Label htmlFor="launch-flow-ref">Issue ref</Label>
        {issuesNonce !== undefined ? (
          <>
            <SearchableSelect
              key={issuesNonce}
              id="launch-flow-ref"
              value={ref}
              options={[]}
              onChange={(v) => setRef(v.trim())}
              allowCustom
              mapOption={(o) => (o.split(" — ")[0] ?? o).trim()}
              fetchOptions={async () => {
                const res = await api.issues()
                return {
                  options: res.issues.map((i) =>
                    i.title ? `${i.key} — ${i.title}` : i.key,
                  ),
                  warning: res.warning ?? undefined,
                }
              }}
            />
            <p className="text-xs text-muted-foreground">
              Pick a recent issue or type any ref; free text is kept.
            </p>
          </>
        ) : (
          <Input
            id="launch-flow-ref"
            value={ref}
            onChange={(e) => setRef(e.target.value)}
            placeholder="IPG-932, OWNER/REPO#22, or https://…/issues/22"
            autoComplete="off"
            spellCheck={false}
          />
        )}
      </div>
      <div>
        <Button
          type="button"
          disabled={!template || !refValue}
          onClick={() => template && setWizard(template)}
        >
          <Workflow aria-hidden />
          {`Run ${template ? template.name : "flow"}…`}
        </Button>
      </div>
      {wizard && refValue ? (
        <FlowWizard
          template={wizard}
          refs={[refValue]}
          onClose={() => setWizard(null)}
        />
      ) : null}
    </>
  )
}

export function Launch() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const qc = useQueryClient()
  const { data: links } = useLinks()
  const { data: repos } = useRepos()
  const modeParam = params.get("mode")
  const refParam = params.get("ref") ?? ""
  const templateParam = params.get("template") ?? ""
  const modeKnown = modeParam === null || MODES.includes(modeParam as Mode)

  const [mode, setMode] = useState<Mode>(
    modeKnown && modeParam !== null ? (modeParam as Mode) : "start",
  )
  // Stored prefs seed run-mode toggles, the start base, and the review fix
  // flags; deep-link URL params (`fixComments=1`, future run-mode params)
  // win over stored values on this mount.
  const [form, setForm] = useState<LaunchForm>(() => {
    const initialMode: Mode = modeKnown && modeParam !== null ? (modeParam as Mode) : "start"
    const stored = loadLaunchPrefs()
    const base: LaunchForm = { ...INITIAL, ...prefsForMode(stored, initialMode) }
    base.ref = modeKnown ? refParam : ""
    if (modeParam === "review") {
      const urlFix = params.get("fixComments") === "1"
      base.fixComments = urlFix || base.fixComments
      if (urlFix) base.reviewAction = "fix"
    }
    return base
  })
  const [submitting, setSubmitting] = useState(false)
  const [templatePicker, setTemplatePicker] = useState(false)
  const [wizardTemplate, setWizardTemplate] = useState<FlowTemplateRow | null>(null)
  const [refreshingIssues, setRefreshingIssues] = useState(false)
  const [issuesNonce, setIssuesNonce] = useState(0)
  const [prefillChecked, setPrefillChecked] = useState(false)
  async function handleRefreshIssues() {
    setRefreshingIssues(true)
    try {
      const fresh = await qc.fetchQuery({
        queryKey: [...queryKeys.issues(), true],
        queryFn: () => api.issues({ force: true }),
        staleTime: 0,
      })
      qc.setQueryData(queryKeys.issues(), fresh)
      // Remount the Issue ref dropdown so its first-open fetch reloads
      // the now-fresh server cache immediately.
      setIssuesNonce((n) => n + 1)
      toast.success("Issues re-fetched live")
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setRefreshingIssues(false)
    }
  }
  // Unknown prefill → blank form + warning, never a crash: an unrecognized
  // mode, or a sync key matching no linked worktree (stale Dashboard link).
  useEffect(() => {
    if (prefillChecked) return
    if (modeParam !== null && !MODES.includes(modeParam as Mode)) {
      setPrefillChecked(true)
      setForm((f) => ({ ...f, ref: "" }))
      toast.warning(`Unknown launch mode "${modeParam}" — form left blank`)
      return
    }
    if ((modeParam === "sync" || modeParam === "review") && refParam && links) {
      setPrefillChecked(true)
      const entry = links.worktrees[refParam]
      if (modeParam === "sync" && !entry) {
        setForm((f) => (f.ref === refParam ? { ...f, ref: "" } : f))
        toast.warning(`No linked worktree for "${refParam}" — form left blank`)
      } else if (entry?.repo) {
        // Review/Sync arrive from a worktree row: the worktree's repo wins.
        const repo: string = entry.repo
        setForm((f) => (f.repo ? f : { ...f, repo }))
      } else {
        // Review of a PR ref (no linked worktree): ask the backend, which
        // resolves linked-worktree → single-linked-tracker repo.
        api.defaultRepo(refParam).then(
          (r) => {
            if (r.repo) setForm((f) => (f.repo ? f : { ...f, repo: r.repo }))
          },
          () => {},
        )
      }
    }
  }, [prefillChecked, modeParam, refParam, links])
  // Transient URL-forced fix (`?mode=review&fixComments=1`) stays
  // live-session-only: it wins over stored prefs on mount but is never
  // written back until the user explicitly picks a review action.
  const urlForcedFix = useRef(modeParam === "review" && params.get("fixComments") === "1")
  /** Restore the incoming tab's stored slice so the shared `runMode` (and
   * the start base) never smear one tab's values into another tab's stored
   * slice. `ref`/`repo`/free text are untouched; the URL-forced fix action
   * is preserved live while transient. */
  function applyModeSlice(next: Mode) {
    const stored = loadLaunchPrefs()
    setForm((f) => {
      const merged: LaunchForm = { ...f, ...prefsForMode(stored, next) }
      if (next === "review" && urlForcedFix.current) {
        merged.reviewAction = f.reviewAction
        merged.fixComments = f.fixComments
      }
      merged.base = next === "start" ? stored.start.base : ""
      return merged
    })
  }
  function update<K extends keyof LaunchForm>(key: K, value: LaunchForm[K]) {
    if (key === "reviewAction" || key === "fixComments") urlForcedFix.current = false
    // PR/MR-shaped ref typed into the Start tab: switch to Review (toast +
    // keep the text; the user can tab back — never auto-switched back).
    if (mode === "start" && key === "ref" && typeof value === "string" && isPrShaped(value)) {
      setMode("review")
      applyModeSlice("review")
      setForm((f) => ({ ...f, [key]: value }))
      toast.info("PR/MR ref detected — switched to Review")
      return
    }
    setForm((f) => ({ ...f, [key]: value }))
  }


  // Persist only the active tab's stored slice on user edits; the incoming
  // tab's slice is rehydrated on switch so the shared `runMode` never
  // smears across tabs. The URL-forced fix action stays live-session-only:
  // sibling review edits still persist, but the stored fix action is kept
  // until the user explicitly picks one. URL params still win for the live
  // session.
  useEffect(() => {
    const prev = loadLaunchPrefs()
    if (mode === "start") {
      saveLaunchPrefs({ ...prev, start: { runMode: form.runMode, base: form.base } })
    } else if (mode === "review") {
      saveLaunchPrefs({
        ...prev,
        review: {
          runMode: form.runMode,
          thenFix: form.thenFixComments,
          fixAction: urlForcedFix.current ? prev.review.fixAction : form.reviewAction,
          useReviewSession: form.useReviewSession,
        },
      })
    } else {
      saveLaunchPrefs({ ...prev, sync: { merge: form.merge } })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, form.runMode, form.base, form.reviewAction, form.thenFixComments, form.useReviewSession, form.merge])

  function switchMode(next: Mode) {
    setMode(next)
    applyModeSlice(next)
  }

  // Repo is mandatory: prefill from /api/default-repo (linked-worktree repo
  // wins, else the single linked repo) whenever it returns one; the user can
  // always override. No "auto" pseudo-option — the dropdown holds a real repo.
  const refValue = form.ref.trim()
  const repoValue = form.repo.trim() || undefined
  const copy = COPY[mode]
  const repoNames = useMemo(() => (repos ?? []).map((r) => r.name), [repos])
  function buildArgs(): string[] {
    if (mode === "sync") {
      return [refValue, ...(form.merge ? ["--merge"] : []), ...extraPromptArgs(form)]
    }
    if (mode === "start") return buildStartArgs(form)
    const args = [refValue]
    // Preview/terminal are TTY copy-paste: no -p flag. Headless
    // (--no-tty, omp -p --auto-approve) only when actually running now.
    if (form.fixComments && form.useReviewSession) args.push("--use-review-session")
    if (repoValue) args.push("--repo", repoValue)
    if (form.depth.trim()) args.push("--depth", form.depth.trim())
    if (form.noStatus) args.push("--no-status")
    args.push(...runModeArgs(form.runMode))
    args.push(...extraPromptArgs(form))
  }

  const extra = form.extraPrompt.trim()
  const flagList = mode === "sync"
    ? [form.merge ? "--merge (local merge)" : "remote rebase (default)", extra ? `--extra-prompt ${extra}` : null].filter((v): v is string => v !== null)
    : [
        form.runMode === "headless" ? "headless (--no-tty)" : form.runMode === "terminal" ? "terminal (--terminal)" : "tty preview (no --no-tty)",
        mode === "review" && form.reviewAction === "fix" ? "--fix-comments (fix open review comments)" : null,
        mode === "review" && form.reviewAction === "review" && form.thenFixComments ? "--then-fix-comments (chain fix after review)" : null,
        repoValue ? `--repo ${repoValue}` : "repo: pick a repo",
        `--depth ${form.depth.trim() || "7"}`,
        mode === "start" && form.base.trim() ? `--base ${form.base.trim()}` : "base: repo default",
        form.runMode === "headless" ? "--launch (run the agent now)"
          : form.runMode === "terminal" ? "--terminal (open the agent in the OS terminal)"
          : "preview (print command, no run)",
        extra ? `--extra-prompt ${extra}` : null,
      ].filter((v): v is string => v !== null)
  async function handleSubmit() {
    if (!refValue) return
    if (mode !== "sync" && !repoValue) {
      toast.error("Pick a repo — none matches this ref (the server CWD is never used).")
      return
    }
    const ok = await confirm({
      action: mode,
      title: `Launch ${copy.label}`,
      description: copy.confirmDesc,
      warning:
        mode === "sync"
          ? "The server appends --yes: sync runs without prompts (AI-assisted conflict resolution if the rebase/merge conflicts)."
          : form.runMode === "headless"
            ? "The agent runs headless with auto-approve (--no-tty): it can commit, push and open MRs/PRs without further prompts. The server appends --yes."
            : form.runMode === "terminal"
              ? "The agent runs in an OS terminal opened on your desktop (workagent --terminal). The server appends --yes."
              : "Preview only: prints the TTY harness command (no -p flag) without running the agent. The server appends --yes.",
      confirmLabel: "Launch",
      details: [
        { label: "Ref", value: refValue, mono: true },
        ...(repoValue ? [{ label: "Repo", value: repoValue, mono: true }] : []),
        { label: "Flags", value: flagList.join(", ") },
      ],
    })
    if (!ok) return
    setSubmitting(true)
    try {
      const { run_id } = await createRun.mutateAsync({
        command: mode,
        args: buildArgs(),
        confirm: true,
      })
      toast.success(`${copy.label} launched`, {
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
        title="Launch"
        description="Start an agent from an issue ref/URL, review a PR/MR ref/URL, run a flow template, or sync a linked worktree. Runs headless as a child process of workagent serve."
        actions={null}
      />
      <Card className="mx-auto max-w-2xl">
        <CardHeader>
          <div>
            <CardTitle>{copy.title}</CardTitle>
            <CardDescription>{copy.refHint}</CardDescription>
          </div>
          <div
            role="tablist"
            aria-label="Launch mode"
            className="mt-3 inline-flex max-w-full flex-wrap rounded-lg border p-1"
          >
            {MODES.map((m) => (
              <button
                key={m}
                role="tab"
                aria-selected={mode === m}
                onClick={() => switchMode(m)}
                className={cn(
                  "min-h-9 shrink-0 rounded-md px-4 text-sm font-medium transition-colors",
                  mode === m
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {COPY[m].label}
              </button>
            ))}
          </div>
        </CardHeader>
        <CardContent className="grid min-h-[420px] content-start gap-5">
          {mode === "start" ? (
            <StartFormFields form={form} onChange={update} idPrefix="launch" issuesNonce={mode === "start" && !refParam ? issuesNonce : undefined} />
          ) : mode === "flow" ? (
            <FlowTabFields
              key={templateParam}
              issuesNonce={!refParam ? issuesNonce : undefined}
              initialTemplate={templateParam || undefined}
            />
          ) : (
            <LaunchOtherFields mode={mode} form={form} onChange={update} refParam={refParam} issuesNonce={issuesNonce} copy={copy} repoNames={repoNames} />
          )}

          {mode === "flow" ? null : (
          <>
          <div className="flex flex-wrap gap-x-6 gap-y-3">
            {mode === "review" ? (
              <RunModeSelect
                idPrefix="launch"
                value={form.runMode}
                onChange={(v) => update("runMode", v)}
              />
            ) : null}
            {mode === "review" ? (
              <ReviewActionSelect
                idPrefix="launch"
                value={form.reviewAction}
                onChange={(v) => {
                  update("reviewAction", v)
                  update("fixComments", v === "fix")
                  if (v === "review") update("thenFixComments", false)
                }}
              />
            ) : null}
            {mode === "review" && form.reviewAction === "review" ? (
              <CheckRow
                id="launch-then-fix-comments"
                checked={form.thenFixComments}
                onChange={(v) => update("thenFixComments", v)}
                label="Then fix comments"
                flag="--then-fix-comments"
                description="After a successful review, chain a fix-comments run in the same session (headless runs it; preview/terminal print the follow-up)."
              />
            ) : null}
            {mode === "review" && form.fixComments ? (
              <CheckRow
                id="launch-use-review-session"
                checked={form.useReviewSession}
                onChange={(v) => update("useReviewSession", v)}
                label="Use review session"
                flag="--use-review-session"
                description="Continue the latest review session instead of starting a fresh fix session."
              />
            ) : null}
            {mode === "review" ? (
              <CheckRow
                id="launch-no-status"
                checked={form.noStatus}
                onChange={(v) => update("noStatus", v)}
                label="Skip status updates"
                flag="--no-status"
                description="Skip automatic tracker issue status transitions."
              />
            ) : null}
          </div>
          {mode === "sync" ? <ExtraPromptField form={form} onChange={update} /> : null}
          <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2.5 text-sm text-amber-800 dark:text-amber-200">
            <p className="flex items-start gap-2">
              <ShieldAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
              <span>
                {mode === "sync" ? (
                  <>
                    The web server appends <span className="font-mono text-[13px]">--yes</span>, so sync runs
                    without prompts (AI-assisted conflict resolution if the rebase/merge conflicts).
                  </>
                ) : form.runMode === "headless" ? (
                  <>
                    Headless auto-approve: the web server runs{" "}
                    <span className="font-mono text-[13px]">--no-tty</span> (
                    <span className="font-mono text-[13px]">omp -p --auto-approve</span>
                    ), so the agent can commit, push and open MRs/PRs on its own.
                  </>
                ) : form.runMode === "terminal" ? (
                  <>
                    The web server opens an OS terminal running the agent command
                    (<span className="font-mono text-[13px]">--terminal</span>); the
                    agent runs with your desktop's environment.
                  </>
                ) : (
                  <>
                    Preview only: prints the TTY harness command (no{" "}
                    <span className="font-mono text-[13px]">-p</span> flag) without running the agent.
                    Pick “Run headless” to run now with{" "}
                    <span className="font-mono text-[13px]">--no-tty</span>.
                  </>
                )}
              </span>
            </p>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => void handleRefreshIssues()}
                disabled={refreshingIssues}
              >
                <RefreshCw className={refreshingIssues ? "animate-spin" : undefined} aria-hidden />
                Refresh issues
              </Button>
              {refValue ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setTemplatePicker(true)}
                >
                  <Workflow aria-hidden />
                  Run a flow…
                </Button>
              ) : null}
            </div>
            <Button
                type="button"
                onClick={() => void handleSubmit()}
                disabled={!refValue || submitting || (mode !== "sync" && !repoValue)}
                title={
                  mode !== "sync" && refValue && !repoValue
                    ? "Pick a repo — none matches this ref"
                    : undefined
                }
              >
                <Rocket aria-hidden />
                {submitting ? "Launching…" : `Launch ${copy.label}`}
              </Button>
          </div>
          </>
          )}
        </CardContent>
      </Card>
      {templatePicker ? (
        <TemplatePickerDialog
          onClose={() => setTemplatePicker(false)}
          onPick={(t) => {
            setTemplatePicker(false)
            setWizardTemplate(t)
          }}
        />
      ) : null}
      {wizardTemplate && refValue ? (
        <FlowWizard
          template={wizardTemplate}
          refs={[refValue]}
          onClose={() => setWizardTemplate(null)}
        />
      ) : null}
    </div>
  )
}
