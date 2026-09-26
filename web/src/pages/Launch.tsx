import { useEffect, useMemo, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { RefreshCw, Rocket, ShieldAlert } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { SearchableSelect } from "@/components/SearchableSelect"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useConfirm } from "@/lib/confirm"
import { errorText } from "@/components/StatusFeedback"
import { queryKeys, useCreateRun, useLinks, useRepos } from "@/lib/queries"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

import { COPY, INITIAL, MODES } from "@/lib/launchConfig"
import type { LaunchForm, Mode } from "@/lib/launchConfig"
import { CheckRow, FieldHelp } from "@/components/FieldHelp"
import { StartFormFields, buildStartArgs } from "@/components/StartForm"

/** Review/Sync fields (kept on the Launch page; Start lives in StartForm). */
function LaunchOtherFields({ mode, form, onChange, copy, repoNames }: {
  mode: Mode
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
      <div className="grid gap-5 sm:grid-cols-2">
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
        <div className="grid gap-2">
          <Label htmlFor="launch-depth">
            <FieldHelp label="Clone depth" flag="--depth" description="Git clone depth for the new worktree (default 7)." />
          </Label>
          <Input
            id="launch-depth"
            type="number"
            min={1}
            value={form.depth}
            onChange={(e) => onChange("depth", e.target.value)}
            placeholder="7"
          />
        </div>
      </div>
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
  const modeKnown = modeParam === null || MODES.includes(modeParam as Mode)

  const [mode, setMode] = useState<Mode>(
    modeKnown && modeParam !== null ? (modeParam as Mode) : "start",
  )
  const [form, setForm] = useState<LaunchForm>(() => ({
    ...INITIAL,
    ref: modeKnown ? refParam : "",
    fixComments: modeParam === "review" && params.get("fixComments") === "1",
  }))
  const [submitting, setSubmitting] = useState(false)
  const [refreshingIssues, setRefreshingIssues] = useState(false)
  const [issuesNonce, setIssuesNonce] = useState(0)
  const [prefillChecked, setPrefillChecked] = useState(false)
  async function handleRefreshIssues() {
    setRefreshingIssues(true)
    try {
      const fresh = await qc.fetchQuery({
        queryKey: [...queryKeys.issues, true],
        queryFn: () => api.issues({ force: true }),
        staleTime: 0,
      })
      qc.setQueryData(queryKeys.issues, fresh)
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
    if (modeParam === "sync" && refParam && links) {
      setPrefillChecked(true)
      if (!(refParam in links.worktrees)) {
        setForm((f) => (f.ref === refParam ? { ...f, ref: "" } : f))
        toast.warning(`No linked worktree for "${refParam}" — form left blank`)
      }
    }
  }, [prefillChecked, modeParam, refParam, links])
  const refValue = form.ref.trim()
  function update<K extends keyof LaunchForm>(key: K, value: LaunchForm[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }


  function switchMode(next: Mode) {
    setMode(next)
    setForm((f) => ({ ...f, base: "" }))
  }

  // Repo is mandatory: prefill from /api/default-repo (linked-worktree repo
  // wins, else the single linked repo) whenever it returns one; the user can
  // always override. No "auto" pseudo-option — the dropdown holds a real repo.
  const repoValue = form.repo.trim() || undefined
  const copy = COPY[mode]
  const repoNames = useMemo(() => (repos ?? []).map((r) => r.name), [repos])
  function buildArgs(): string[] {
    if (mode === "sync") {
      return [refValue, ...(form.merge ? ["--merge"] : [])]
    }
    if (mode === "start") return buildStartArgs(form)
    const args = [refValue, "--no-tty"]
    if (form.fixComments) args.push("--fix-comments")
    if (repoValue) args.push("--repo", repoValue)
    if (form.depth.trim()) args.push("--depth", form.depth.trim())
    if (form.launch) args.push("--launch")
    return args
  }

  const flagList = mode === "sync"
    ? [form.merge ? "--merge (local merge)" : "remote rebase (default)"]
    : [
        "headless (--no-tty)",
        mode === "review" && form.fixComments ? "--fix-comments (fix open review comments)" : null,
        repoValue ? `--repo ${repoValue}` : "repo: pick a repo",
        `--depth ${form.depth.trim() || "7"}`,
        mode === "start" && form.base.trim() ? `--base ${form.base.trim()}` : "base: repo default",
        form.launch ? "--launch (run the agent now)" : "preview (print command, no run)",
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
          : "The agent runs headless with auto-approve (--no-tty): it can commit, push and open MRs/PRs without further prompts. The server appends --yes.",
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
        description="Start an agent from an issue ref/URL, review a PR/MR ref/URL, or sync a linked worktree. Runs headless as a child process of workagent serve."
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void handleRefreshIssues()}
              disabled={refreshingIssues}
            >
              <RefreshCw className={refreshingIssues ? "animate-spin" : undefined} aria-hidden />
              Refresh issues
            </Button>
            <div
              role="tablist"
              aria-label="Launch mode"
              className="inline-flex rounded-lg border p-1"
            >
              {MODES.map((m) => (
                <button
                  key={m}
                  role="tab"
                  aria-selected={mode === m}
                  onClick={() => switchMode(m)}
                  className={cn(
                    "min-h-9 rounded-md px-4 text-sm font-medium transition-colors",
                    mode === m
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {COPY[m].label}
                </button>
              ))}
            </div>
          </>
        }
      />
      <Card className="mx-auto max-w-2xl">
        <CardHeader>
          <CardTitle>{copy.title}</CardTitle>
          <CardDescription>{copy.refHint}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-5">
          {mode === "start" ? (
            <StartFormFields form={form} onChange={update} idPrefix="launch" issuesNonce={mode === "start" && !refParam ? issuesNonce : undefined} />
          ) : (
            <LaunchOtherFields mode={mode} form={form} onChange={update} refParam={refParam} issuesNonce={issuesNonce} copy={copy} repoNames={repoNames} />
          )}

          <div className="flex flex-wrap gap-x-6 gap-y-3">
            {mode !== "sync" ? (
              <CheckRow
                id="launch-launch"
                checked={form.launch}
                onChange={(v) => update("launch", v)}
                label="Run agent now"
                flag="--launch"
                description="Run the agent now (default: print the command and hand over the worktree)."
              />
            ) : null}
            {mode === "review" ? (
              <CheckRow
                id="launch-fix-comments"
                checked={form.fixComments}
                onChange={(v) => update("fixComments", v)}
                label="Fix review comments"
                flag="--fix-comments"
                description="Fix open review comments: validate, apply, resolve/close, commit and push."
              />
            ) : null}
          </div>

          <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2.5 text-sm text-amber-800 dark:text-amber-200">
            <p className="flex items-start gap-2">
              <ShieldAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
              <span>
                {mode === "sync" ? (
                  <>
                    The web server appends <span className="font-mono text-[13px]">--yes</span>, so sync runs
                    without prompts (AI-assisted conflict resolution if the rebase/merge conflicts).
                  </>
                ) : (
                  <>
                    Headless auto-approve: the web server always runs{" "}
                    <span className="font-mono text-[13px]">--no-tty</span> (
                    <span className="font-mono text-[13px]">omp -p --auto-approve</span>
                    ), so the agent can commit, push and open MRs/PRs on its own.
                  </>
                )}
              </span>
            </p>
          </div>

          <div className="flex justify-end gap-2">
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
        </CardContent>
      </Card>
    </div>
  )
}
