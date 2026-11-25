import { useEffect, useMemo, useRef, useState } from "react"
import { Link, useParams } from "react-router-dom"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  ArrowLeft,
  ChevronRight,
  FlaskConical,
  GitBranch,
  Info,
  PanelRightClose,
  PanelRightOpen,
  Send,
  Workflow,
} from "lucide-react"
import { AcpTranscript } from "@/components/AcpTranscript"
import { SpawnedWorkersCard } from "@/components/SpawnedWorkersCard"
import { WorktreeWatcherOverrides } from "@/components/WorktreeWatcherOverrides"
import { HarnessPicker } from "@/components/HarnessPicker"
import { QuestionCard } from "@/components/QuestionCard"
import { RunModeSelect } from "@/components/StartForm"
import { CiBadge, PrBadge, RunStateBadge } from "@/components/StateBadge"
import { EmptyState, ErrorState, errorText } from "@/components/StatusFeedback"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { mergeLines, useRunEvents } from "@/hooks/useRunEvents"
import { api, prLabel, prUrl, type Run } from "@/lib/api"
import { relativeTime, shortId } from "@/lib/format"
import { hasAcpStructuredEvents } from "@/lib/harness"
import type { RunMode } from "@/lib/launchConfig"
import { isFailingCheck, resolveFixCiPrompt } from "@/lib/fixCiPrompt"
import { queryKeys, useFlows, useRun, useRuns, useStatusAll, useWorktreeTimeline } from "@/lib/queries"

type InspectorTab = "summary" | "flows" | "prodtest"

const QUICK_CHIPS = [
  { label: "Fix CI", prompt: "Inspect the failing CI checks and fix the root cause." },
  { label: "Fix review comments", prompt: "Address the unresolved review comments on this PR/MR." },
  { label: "Run tests", prompt: "Run the test suite and report any failures." },
  { label: "Summarize status", prompt: "Summarize current branch changes, test status, and next steps." },
] as const

function runBelongsToWorktree(run: Run, refKey: string, worktreePath: string): boolean {
  if (run.worktree && worktreePath && run.worktree === worktreePath) return true
  if (run.target && (run.target.endsWith(`:${refKey}`) || run.target === refKey)) return true
  if (Array.isArray(run.args) && run.args.includes(refKey)) return true
  return false
}

export function WorktreeWorkspace() {
  const { ref: rawRef = "" } = useParams()
  const refKey = useMemo(() => {
    try {
      return decodeURIComponent(rawRef)
    } catch {
      return rawRef
    }
  }, [rawRef])

  const qc = useQueryClient()
  const statusQ = useStatusAll()
  const runsQ = useRuns()
  const flowsQ = useFlows(refKey)

  const entry = statusQ.data?.[refKey]
  const timelineQ = useWorktreeTimeline(entry?.ci === "failure" ? refKey : undefined)
  const worktreePath = entry?.worktree ?? ""

  const worktreeRuns = useMemo(() => {
    const all = runsQ.data ?? []
    return all
      .filter((r) => runBelongsToWorktree(r, refKey, worktreePath))
      .sort((a, b) => (b.created ?? 0) - (a.created ?? 0))
  }, [runsQ.data, refKey, worktreePath])

  const worktreeFlows = useMemo(() => {
    const all = flowsQ.data?.flows ?? []
    return all.filter((f) => f.worktree_ref === refKey)
  }, [flowsQ.data, refKey])

  const activeRefKeyRef = useRef(refKey)
  activeRefKeyRef.current = refKey
  const ciFetchTokenRef = useRef(0)

  const [selectedRunId, setSelectedRunId] = useState<string>("")
  const [isFetchingCiLogs, setIsFetchingCiLogs] = useState(false)
  useEffect(() => {
    setSelectedRunId("")
    ciFetchTokenRef.current += 1
    setIsFetchingCiLogs(false)
  }, [refKey])
  const activeRunId = selectedRunId || worktreeRuns[0]?.id || ""

  const runQ = useRun(activeRunId)
  const isRunning = runQ.data?.state === "running"
  const events = useRunEvents(isRunning ? activeRunId : "")
  const mergedLines = useMemo(
    () => mergeLines(runQ.data?.lines ?? [], events.lines),
    [runQ.data?.lines, events.lines],
  )
  const hasAcp = useMemo(() => hasAcpStructuredEvents(mergedLines), [mergedLines])

  const [inspectorOpen, setInspectorOpen] = useState(true)
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("summary")

  // Follow-up composer state
  const [prompt, setPrompt] = useState("")
  const [harness, setHarness] = useState("")
  const [runMode, setRunMode] = useState<RunMode>("acp")
  const [model, setModel] = useState("")
  const [effort, setEffort] = useState("")

  const sendFollowUp = useMutation({
    mutationFn: async () => {
      const trimmed = prompt.trim()
      if (!trimmed) throw new Error("Prompt is required")
      if (activeRunId) {
        return api.followUpRun(activeRunId, {
          prompt: trimmed,
          ...(harness ? { harness } : {}),
          run_mode: runMode,
          ...(model.trim() ? { model: model.trim() } : {}),
          ...(effort.trim() ? { effort: effort.trim() } : {}),
        })
      }
      const args = [refKey, "--prompt", trimmed, "--run-mode", runMode, "--yes"]
      if (harness) args.push("--harness", harness)
      if (model.trim()) args.push("--model", model.trim())
      if (effort.trim()) args.push("--effort", effort.trim())
      const created = await api.createRun({ command: "adhoc", args, confirm: true })
      return { run_id: created.run_id, continued_from_run_id: null, worktree_ref: refKey }
    },
    onSuccess: (res) => {
      setPrompt("")
      setSelectedRunId(res.run_id)
      toast.success("Follow-up run started")
      void qc.invalidateQueries({ queryKey: queryKeys.runs })
      void qc.invalidateQueries({ queryKey: queryKeys.statusAll })
    },
    onError: (err: unknown) => toast.error(errorText(err)),
  })

  if (statusQ.isError) {
    return <ErrorState error={statusQ.error} onRetry={() => void statusQ.refetch()} />
  }

  return (
    <div className="flex flex-col gap-4" data-testid="worktree-workspace">
      {/* Breadcrumb & Workspace Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b pb-3">
        <div className="min-w-0">
          <nav aria-label="Breadcrumb" className="mb-1 flex items-center gap-1.5 text-xs text-muted-foreground">
            <Link to="/" className="inline-flex items-center gap-1 hover:text-foreground">
              <ArrowLeft className="size-3.5" aria-hidden />
              Dashboard
            </Link>
            <ChevronRight className="size-3.5" aria-hidden />
            <span className="truncate font-mono text-foreground">{refKey}</span>
          </nav>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-lg font-semibold tracking-tight">
              {entry?.pr_detail?.title || entry?.issue || refKey}
            </h1>
            {entry?.display_status ? (
              <Badge variant="outline">{entry.display_status}</Badge>
            ) : null}
            {entry?.branch ? (
              <span className="inline-flex items-center gap-1 rounded bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground">
                <GitBranch className="size-3" aria-hidden />
                {entry.branch}
              </span>
            ) : null}
            {entry?.pr_detail ? <PrBadge pr={entry.pr_detail} /> : null}
            {entry?.ci ? <CiBadge ci={entry.ci} /> : null}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setInspectorOpen((v) => !v)}
            aria-label={inspectorOpen ? "Collapse inspector" : "Expand inspector"}
          >
            {inspectorOpen ? (
              <>
                <PanelRightClose className="mr-1.5 size-4" aria-hidden />
                Hide Inspector
              </>
            ) : (
              <>
                <PanelRightOpen className="mr-1.5 size-4" aria-hidden />
                Inspector
              </>
            )}
          </Button>
        </div>
      </div>

      {/* Split Pane: Center Transcript + Composer | Right Collapsible Inspector Rail */}
      <div className={inspectorOpen ? "grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]" : "grid gap-4"}>
        {/* Center Pane */}
        <section className="flex min-w-0 flex-col gap-3" aria-label="Agent transcript and composer">
          {/* Active run strip */}
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-card px-3 py-2 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-muted-foreground">Active run:</span>
              {activeRunId ? (
                <>
                  <Link
                    to={`/runs/${encodeURIComponent(activeRunId)}`}
                    className="font-mono font-medium text-primary hover:underline"
                  >
                    {shortId(activeRunId)}
                  </Link>
                  {runQ.data ? <RunStateBadge state={runQ.data.state} /> : null}
                </>
              ) : (
                <span className="text-muted-foreground">No runs yet</span>
              )}
            </div>
            {worktreeRuns.length > 1 ? (
              <select
                aria-label="Select worktree run"
                value={activeRunId}
                onChange={(e) => setSelectedRunId(e.target.value)}
                className="h-7 rounded border border-input bg-background px-2 font-mono text-xs"
              >
                {worktreeRuns.map((r) => (
                  <option key={r.id} value={r.id}>
                    {shortId(r.id)} · {r.command} ({r.state})
                  </option>
                ))}
              </select>
            ) : null}
          </div>

          {/* Transcript */}
          <div className="min-h-[320px]">
            {!activeRunId ? (
              <EmptyState
                title="No agent runs on this worktree yet"
                description="Send a follow-up prompt below to start an ACP session on this worktree."
              />
            ) : hasAcp ? (
              <AcpTranscript lines={mergedLines} spawnedRuns={runQ.data?.spawned_runs} />
            ) : (
              <div className="space-y-3">
                {runQ.data?.spawned_runs && runQ.data.spawned_runs.length > 0 ? (
                  <SpawnedWorkersCard workers={runQ.data.spawned_runs} />
                ) : null}
                <div className="log-shell overflow-hidden rounded-lg border">
                <div className="flex items-center justify-between border-b border-white/10 px-3 py-2 text-xs text-white/70">
                  <span>{isRunning ? "Live" : "Finished"}</span>
                  <span>{mergedLines.length} lines</span>
                </div>
                <div className="log-scroll max-h-[50vh] overflow-auto px-3 py-2.5 font-mono text-xs leading-relaxed">
                  {mergedLines.length === 0 ? (
                    <p className="text-white/40">Waiting for output…</p>
                  ) : (
                    mergedLines.map((ln) => (
                      <div key={ln.seq} className="whitespace-pre-wrap break-words">
                        {ln.text || "\u00a0"}
                      </div>
                    ))
                  )}
                </div>
                </div>
              </div>
            )}
          </div>

          {/* Pinned Pending Question Card */}
          {activeRunId && runQ.data?.pending_question ? (
            <QuestionCard runId={activeRunId} question={runQ.data.pending_question} />
          ) : null}

          {/* Persistent Bottom Follow-up Prompt Composer */}
          <Card data-testid="follow-up-composer">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Follow-up Prompt Composer</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {/* Quick action chips */}
              <div className="flex flex-wrap gap-1.5" role="group" aria-label="Quick action chips">
                {QUICK_CHIPS.map((chip) => (
                  <Button
                    key={chip.label}
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs"
                    disabled={chip.label === "Fix CI" && isFetchingCiLogs}
                    onClick={() => {
                      if (chip.label !== "Fix CI") {
                        setPrompt(chip.prompt)
                        return
                      }
                      const timelineChecks = timelineQ.data?.checks ?? []
                      if (timelineChecks.some(isFailingCheck) || entry?.ci === "failure") {
                        const requestRefKey = refKey
                        const requestToken = ++ciFetchTokenRef.current
                        setIsFetchingCiLogs(true)
                        void resolveFixCiPrompt(requestRefKey, timelineChecks, {
                          fallbackCiUrl: entry?.ci_url,
                          fetchIfEmpty: true,
                          queryClient: qc,
                        })
                          .then((resolvedPrompt) => {
                            if (
                              activeRefKeyRef.current === requestRefKey &&
                              ciFetchTokenRef.current === requestToken
                            ) {
                              setPrompt(resolvedPrompt)
                            }
                          })
                          .finally(() => {
                            if (
                              activeRefKeyRef.current === requestRefKey &&
                              ciFetchTokenRef.current === requestToken
                            ) {
                              setIsFetchingCiLogs(false)
                            }
                          })
                        return
                      }
                      setPrompt(chip.prompt)
                    }}
                  >
                    {chip.label === "Fix CI" && isFetchingCiLogs ? "Fetching logs…" : chip.label}
                  </Button>
                ))}
              </div>

              {/* Harness / RunMode / Model / Effort selectors */}
              <div className="flex flex-wrap items-center gap-3">
                <HarnessPicker
                  idPrefix="workspace-followup"
                  harness={harness}
                  model={model}
                  effort={effort}
                  onHarnessChange={(v) => { setHarness(v); setModel(""); setEffort("") }}
                  onModelChange={setModel}
                  onEffortChange={setEffort}
                />
                <RunModeSelect
                  idPrefix="workspace-followup"
                  value={runMode}
                  onChange={setRunMode}
                  hidePreview
                />
              </div>

              {/* Prompt textarea + submit */}
              <div className="flex flex-col gap-2">
                <textarea
                  aria-label="Follow-up prompt"
                  placeholder="Ask the agent to continue, fix tests, address review comments…"
                  rows={3}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  className="flex min-h-[72px] w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-1 focus-visible:ring-ring"
                  onKeyDown={(e) => {
                    if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && prompt.trim() && !sendFollowUp.isPending) {
                      e.preventDefault()
                      sendFollowUp.mutate()
                    }
                  }}
                />
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs text-muted-foreground">
                    {activeRunId
                      ? `Continues from run ${shortId(activeRunId)} (${runMode.toUpperCase()})`
                      : `Starts a new adhoc run on ${refKey}`}
                  </span>
                  <Button
                    type="button"
                    size="sm"
                    disabled={!prompt.trim() || sendFollowUp.isPending}
                    onClick={() => sendFollowUp.mutate()}
                  >
                    <Send className="mr-1.5 size-3.5" aria-hidden />
                    {sendFollowUp.isPending ? "Sending…" : "Send follow-up"}
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </section>

        {/* Right Collapsible Tabbed Inspector Rail */}
        {inspectorOpen ? (
          <aside className="flex min-w-0 flex-col gap-3 rounded-lg border bg-card p-3" aria-label="Worktree inspector">
            <div className="grid grid-cols-3 gap-1 rounded-md bg-muted p-1 text-xs" role="tablist">
              {(
                [
                  { id: "summary", label: "Summary & Timeline", icon: Info },
                  { id: "flows", label: "Flows & Runs", icon: Workflow },
                  { id: "prodtest", label: "Prodtest", icon: FlaskConical },
                ] as const
              ).map(({ id, label, icon: Icon }) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={inspectorTab === id}
                  onClick={() => setInspectorTab(id)}
                  className={[
                    "inline-flex items-center justify-center gap-1 rounded px-2 py-1.5 font-medium transition-colors",
                    inspectorTab === id
                      ? "bg-background text-foreground shadow-xs"
                      : "text-muted-foreground hover:text-foreground",
                  ].join(" ")}
                >
                  <Icon className="size-3.5 shrink-0" aria-hidden />
                  <span className="truncate">{label}</span>
                </button>
              ))}
            </div>

            {inspectorTab === "summary" ? (
              <div className="space-y-3 text-xs" data-testid="inspector-summary">
                <WorktreeWatcherOverrides worktreeKey={refKey} />
                <dl className="space-y-1.5">
                  <div className="flex justify-between gap-2">
                    <dt className="text-muted-foreground">Key</dt>
                    <dd className="truncate font-mono">{refKey}</dd>
                  </div>
                  {entry?.branch ? (
                    <div className="flex justify-between gap-2">
                      <dt className="text-muted-foreground">Branch</dt>
                      <dd className="truncate font-mono">{entry.branch}</dd>
                    </div>
                  ) : null}
                  {entry?.worktree ? (
                    <div className="flex justify-between gap-2">
                      <dt className="text-muted-foreground">Worktree</dt>
                      <dd className="truncate font-mono" title={entry.worktree}>{entry.worktree}</dd>
                    </div>
                  ) : null}
                  {entry?.pr ? (
                    <div className="flex justify-between gap-2">
                      <dt className="text-muted-foreground">PR / MR</dt>
                      <dd>
                        {prUrl(entry) ? (
                          <a href={prUrl(entry)} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                            {prLabel(entry.pr)}
                          </a>
                        ) : (
                          prLabel(entry.pr)
                        )}
                      </dd>
                    </div>
                  ) : null}
                  {entry?.attention_reason ? (
                    <div className="rounded border border-amber-500/30 bg-amber-500/10 p-2 text-amber-700 dark:text-amber-300">
                      {entry.attention_reason}
                    </div>
                  ) : null}
                </dl>
                <div className="border-t pt-2">
                  <h2 className="mb-1.5 font-medium text-foreground">Recent Activity</h2>
                  {worktreeRuns.length === 0 ? (
                    <p className="text-muted-foreground">No recorded runs yet.</p>
                  ) : (
                    <ul className="space-y-1.5">
                      {worktreeRuns.slice(0, 6).map((r) => (
                        <li key={r.id} className="flex items-center justify-between gap-2">
                          <Link to={`/runs/${encodeURIComponent(r.id)}`} className="truncate font-mono hover:underline">
                            {r.command} · {shortId(r.id)}
                          </Link>
                          <RunStateBadge state={r.state} />
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            ) : null}

            {inspectorTab === "flows" ? (
              <div className="space-y-3 text-xs" data-testid="inspector-flows">
                <div>
                  <h2 className="mb-1.5 font-medium text-foreground">Flows ({worktreeFlows.length})</h2>
                  {worktreeFlows.length === 0 ? (
                    <p className="text-muted-foreground">No flows for this worktree.</p>
                  ) : (
                    <ul className="space-y-1.5">
                      {worktreeFlows.map((f) => (
                        <li key={f.id} className="flex items-center justify-between gap-2 rounded border p-2">
                          <span className="truncate font-medium">{f.template_label}</span>
                          <Badge variant="outline">{f.status}</Badge>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div className="border-t pt-2">
                  <h2 className="mb-1.5 font-medium text-foreground">Runs ({worktreeRuns.length})</h2>
                  {worktreeRuns.length === 0 ? (
                    <p className="text-muted-foreground">No runs for this worktree.</p>
                  ) : (
                    <ul className="space-y-1.5">
                      {worktreeRuns.map((r) => (
                        <li key={r.id} className="flex items-center justify-between gap-2">
                          <button
                            type="button"
                            onClick={() => setSelectedRunId(r.id)}
                            className="truncate text-left font-mono hover:underline"
                          >
                            {r.command} · {shortId(r.id)}
                          </button>
                          <span className="shrink-0 text-muted-foreground">
                            {r.created ? relativeTime(r.created) : ""}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            ) : null}

            {inspectorTab === "prodtest" ? (
              <div className="space-y-2 text-xs" data-testid="inspector-prodtest">
                <p className="font-medium text-foreground">Prodtest Plan &amp; Runs</p>
                <p className="text-muted-foreground">
                  Status: <span className="font-mono text-foreground">{entry?.prodtest || "—"}</span>
                </p>
                <Link to="/prodtests" className="inline-block text-primary hover:underline">
                  Open Prodtests page →
                </Link>
              </div>
            ) : null}
          </aside>
        ) : null}
      </div>
    </div>
  )
}
