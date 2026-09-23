import { useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { ArrowLeft, Ban, Copy, Redo, SquareTerminal } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { RunStateBadge } from "@/components/StateBadge"
import { ErrorState, errorText } from "@/components/StatusFeedback"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { mergeLines, useRunEvents } from "@/hooks/useRunEvents"
import { api, type CreateRunInput, type RunDetail as RunDetailData, type RunLine } from "@/lib/api"
import { useConfirm } from "@/lib/confirm"
import { queryKeys, useCancelRun, usePath, useResumeRun, useRun } from "@/lib/queries"
import { copyToClipboard, relativeTime, resumeCommand, shortId } from "@/lib/format"
import { DESTRUCTIVE_COMMANDS, commandAction, commandLabel } from "@/lib/runs"

function LogViewer({
  lines,
  connected,
  terminal,
}: {
  lines: RunLine[]
  connected: boolean
  terminal: boolean
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const programmaticRef = useRef(false)
  const [follow, setFollow] = useState(true)

  useEffect(() => {
    if (!follow) return
    const el = scrollRef.current
    if (!el) return
    programmaticRef.current = true
    el.scrollTop = el.scrollHeight
    window.requestAnimationFrame(() => {
      programmaticRef.current = false
    })
  }, [lines, follow])

  function handleScroll() {
    if (programmaticRef.current) return
    const el = scrollRef.current
    if (!el) return
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    if (distance > 48) setFollow(false)
  }

  return (
    <div className="log-shell overflow-hidden rounded-lg border">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 px-3 py-2 text-xs">
        <span className="flex items-center gap-1.5 text-white/70">
          <span
            aria-hidden
            className={
              connected
                ? "inline-block size-2 rounded-full bg-emerald-400 animate-pulse"
                : terminal
                  ? "inline-block size-2 rounded-full bg-white/30"
                  : "inline-block size-2 rounded-full bg-amber-400"
            }
          />
          {connected ? "Live" : terminal ? "Finished" : "Connecting…"}
        </span>
        <span className="flex items-center gap-4">
          <span className="text-white/50">{lines.length} lines</span>
          <label className="flex items-center gap-2 text-white/80">
            <Checkbox
              checked={follow}
              onCheckedChange={(v) => setFollow(v === true)}
              aria-label="Follow output"
            />
            Follow output
          </label>
        </span>
      </div>
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="log-scroll h-[60vh] overflow-auto px-3 py-2.5 font-mono text-[12.5px] leading-relaxed"
      >
        {lines.length === 0 ? (
          <p className="text-white/40">Waiting for output…</p>
        ) : (
          lines.map((line) => (
            <div key={line.seq} className="whitespace-pre-wrap break-words">
              {line.text || "\u00a0"}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
/** Copy the full cd-prefixed runtime command parsed from the log. */
function RunRuntimeCommandAction({ lines }: { lines: RunLine[] }) {
  const cmd = useMemo(() => {
    // The CLI preview embeds the prompt as one shlex-quoted argv element;
    // prompts contain newlines, so run.append's splitlines() breaks the
    // logged command across lines. Rejoin continuation lines (indented or
    // quote-unbalanced) until quotes balance.
    let start = -1
    for (let i = 0; i < lines.length; i++) {
      if (/runtime command:\s*\S/.test(lines[i]?.text ?? "")) {
        start = i
        break
      }
    }
    if (start < 0) return ""
    const first = (lines[start]?.text ?? "").replace(/^.*runtime command:\s*/, "")
    let cmd = first.trimEnd()
    const unbalanced = (s: string) => (s.match(/'/g) ?? []).length % 2 === 1
    for (let i = start + 1; i < lines.length && unbalanced(cmd); i++) {
      cmd += `\n${lines[i]?.text ?? ""}`
    }
    return cmd.trim()
  }, [lines])
  return (
    <Button
      variant="outline"
      size="sm"
      onClick={() => {
        copyToClipboard(cmd)
          .then(() => toast.success("Runtime command copied"))
          .catch((err: unknown) => toast.error(errorText(err)))
      }}
    >
      <Copy aria-hidden /> Copy runtime command
    </Button>
  )
}
/** Resume/copy buttons for a run that executed a runtime session. */
function RunSessionActions({
  runId,
  target,
  worktree,
  sessionFile,
}: {
  runId: string
  target: string
  worktree: string
  sessionFile: string
}) {
  const resume = useResumeRun()
  const pathQ = usePath(target)
  const wt = worktree || pathQ.data?.worktree || ""
  return (
    <>
      <Button
        variant="outline"
        size="sm"
        disabled={resume.isPending}
        onClick={() => {
          resume
            .mutateAsync(runId)
            .then(() => toast.success("Terminal opened on the session"))
            .catch((err: unknown) => toast.error(errorText(err)))
        }}
      >
        <SquareTerminal aria-hidden /> Resume in terminal
      </Button>
      <Button
        variant="outline"
        size="sm"
        disabled={!wt}
        onClick={() => {
          copyToClipboard(resumeCommand(wt, sessionFile))
            .then(() => toast.success("Resume command copied"))
            .catch((err: unknown) => toast.error(errorText(err)))
        }}
      >
        <Copy aria-hidden /> Copy resume command
      </Button>
    </>
  )
}


export function RunDetail() {
  const { runId = "" } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const cancel = useCancelRun()
  const runQ = useRun(runId)

  const sse = useRunEvents(runId || null, {
    initialLines: runQ.data?.lines,
  })

  // SSE state wins (fresher); the polled detail seeds lines and flags.
  const state = sse.state ?? runQ.data?.state ?? null
  const exitCode = sse.exitCode ?? runQ.data?.exit_code ?? null
  const lines = useMemo(
    () => mergeLines(sse.lines, runQ.data?.lines),
    [sse.lines, runQ.data?.lines],
  )

  // A terminal SSE event updates the cached run so lists stay in sync.
  useEffect(() => {
    if (!sse.state || !runId) return
    qc.setQueryData<RunDetailData>(queryKeys.run(runId), (prev) =>
      prev
        ? {
            ...prev,
            state: sse.state!,
            exit_code: sse.exitCode,
          }
        : prev,
    )
    void qc.invalidateQueries({ queryKey: queryKeys.runs })
  }, [sse.state, sse.exitCode, runId, qc])

  async function handleCancel() {
    try {
      await cancel.mutateAsync(runId)
      toast.success("Cancellation signalled (SIGTERM to the process group)")
    } catch (err) {
      const msg = errorText(err)
      if (msg.includes("409")) toast.info("Run already finished")
      else toast.error(msg)
    }
  }

  async function handleRerun() {
    const run = runQ.data
    if (!run) return
    // Strip the leading -v (re-added per current Settings) and any appended --yes.
    const stripped = run.args[0] === "-v" ? run.args.slice(1) : [...run.args]
    const args = stripped.filter((a) => a !== "--yes")
    const destructive = DESTRUCTIVE_COMMANDS.includes(run.command)
    if (destructive) {
      const ok = await confirm({
        action: commandAction(run.command, args),
        title: `Re-run ${commandLabel(run.command)}${run.target ? ` (${run.target})` : ""}`,
        description: "Same command and arguments as the original run.",
        destructive: true,
        confirmLabel: "Re-run",
        details: [
          { label: "Command", value: run.command, mono: true },
          { label: "Args", value: args.join(" "), mono: true },
        ],
      })
      if (!ok) return
    }
    try {
      const { run_id } = await api.createRun({
        command: run.command as CreateRunInput["command"],
        args,
        confirm: destructive ? true : undefined,
      })
      toast.success("Re-run started")
      navigate(`/runs/${run_id}`)
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  if (runQ.isPending) {
    return (
      <div>
        <PageHeader title="Run" description="Loading run…" />
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            Loading run {shortId(runId)}…
          </CardContent>
        </Card>
      </div>
    )
  }

  if (runQ.isError || !runQ.data) {
    return (
      <div>
        <PageHeader title="Run" description="The run could not be loaded." />
        <ErrorState error={runQ.error} onRetry={() => void runQ.refetch()} />
      </div>
    )
  }

  const run = runQ.data
  const isRunning = state === "running"
  const isNeedsInput = state === "needs_input"
  const terminal = state !== null && state !== "running"

  return (
    <div>
      <PageHeader
        title={`Run ${shortId(runId)}`}
        description={`${commandLabel(run.command)} · started ${relativeTime(run.created)} · target ${run.target}`}
        actions={
          <>
            <Button variant="ghost" size="sm" asChild>
              <Link to={`/runs?target=${encodeURIComponent(run.target)}`}>
                <ArrowLeft aria-hidden /> Same worktree runs
              </Link>
            </Button>
            <Button variant="ghost" size="sm" asChild>
              <Link to="/runs">
                <ArrowLeft aria-hidden /> All runs
              </Link>
            </Button>
            {run.session_file ? (
              <RunSessionActions
                runId={run.id}
                target={run.target}
                worktree={run.worktree}
                sessionFile={run.session_file}
              />
            ) : null}
            <RunRuntimeCommandAction lines={lines} />
            {isRunning ? (
              <Button
                variant="destructive"
                size="sm"
                onClick={() => void handleCancel()}
                disabled={cancel.isPending}
              >
                <Ban aria-hidden /> Cancel
              </Button>
            ) : null}
            {isNeedsInput ? (
              <Button size="sm" onClick={() => void handleRerun()}>
                <Redo aria-hidden /> Re-run
              </Button>
            ) : null}
          </>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        {state ? <RunStateBadge state={state} /> : null}
        <span className="text-sm text-muted-foreground">
          exit code: <span className="font-mono">{exitCode ?? "—"}</span>
        </span>
        <span className="font-mono text-[13px] text-muted-foreground">
          harness {run.command} {run.args.join(" ")}
        </span>
        {run.truncated ? (
          <span className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-xs text-amber-700 dark:text-amber-300">
            output truncated server-side
          </span>
        ) : null}
      </div>

      {isNeedsInput ? (
        <Card className="mb-4 border-amber-500/40">
          <CardHeader>
            <CardTitle className="text-base">Needs input</CardTitle>
            <CardDescription>
              The command exited with code 2 — usually an unparseable ref, no
              linked state, a missing flag, or a --force-required conflict.
              Check the captured output below, then re-run with the same
              arguments.
            </CardDescription>
          </CardHeader>
        </Card>
      ) : null}

      <LogViewer lines={lines} connected={sse.connected} terminal={terminal} />

      <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
        <SquareTerminal className="size-3.5" aria-hidden />
        Log via SSE (/api/runs/{runId}/events) — reconnects resume with
        Last-Event-ID; ANSI is stripped server-side.
      </p>
    </div>
  )
}
