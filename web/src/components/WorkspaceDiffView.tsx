import { useEffect, useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import {
  FileCode2,
  MessageSquarePlus,
  Send,
  Trash2,
  AlertTriangle,
} from "lucide-react"
import type { DiffHunk, DiffLine, DiffMode, WorktreeDiffFile } from "@/lib/api"
import { useCreateRun, useWorktreeDiff, useWorktreeFiles } from "@/lib/queries"
import { HighlightedLine } from "@/lib/syntaxHighlight"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { EmptyState, ErrorState, errorText } from "@/components/StatusFeedback"

export interface LineAnnotation {
  id: string
  path: string
  line: number
  side: "old" | "new"
  code: string
  comment: string
}

export type DiffLayout = "unified" | "split"

/** Pure helper (unit-tested): format staged inline line comments into a single agent prompt. */
export function formatAnnotationsPrompt(annotations: LineAnnotation[]): string {
  const header = "Please address the following inline diff review comments:"
  const body = annotations.map((a, idx) => {
    const sideLabel = a.side === "old" ? "old line" : "line"
    const snippet = a.code.trim() ? `\n   \`${a.code.trim()}\`` : ""
    return `${idx + 1}. \`${a.path}\` (${sideLabel} ${a.line}): ${a.comment}${snippet}`
  })
  return [header, "", ...body].join("\n")
}

interface SplitRow {
  left: DiffLine | null
  right: DiffLine | null
}

/** Pair consecutive del/add runs into side-by-side rows for Split view. */
export function buildSplitRows(hunk: DiffHunk): SplitRow[] {
  const rows: SplitRow[] = []
  let i = 0
  const lines = hunk.lines
  while (i < lines.length) {
    const cur = lines[i]
    if (!cur) break
    if (cur.type === "context" || cur.type === "meta") {
      rows.push({ left: cur, right: cur })
      i += 1
      continue
    }
    const dels: DiffLine[] = []
    const adds: DiffLine[] = []
    while (i < lines.length) {
      const ln = lines[i]
      if (!ln || ln.type !== "del") break
      dels.push(ln)
      i += 1
    }
    while (i < lines.length) {
      const ln = lines[i]
      if (!ln || ln.type !== "add") break
      adds.push(ln)
      i += 1
    }
    const maxLen = Math.max(dels.length, adds.length)
    for (let k = 0; k < maxLen; k += 1) {
      rows.push({ left: dels[k] ?? null, right: adds[k] ?? null })
    }
  }
  return rows
}

const STATUS_BADGE: Record<string, string> = {
  M: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  A: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  "?": "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  D: "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300",
  R: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
}

export function WorkspaceDiffView({
  worktreeRef,
  onDispatchAnnotations,
  className = "",
}: {
  worktreeRef: string
  /** Optional hook for T07 WorktreeWorkspace follow-up composer; defaults to dispatching `adhoc --acp`. */
  onDispatchAnnotations?: (prompt: string, annotations: LineAnnotation[]) => void | Promise<void>
  className?: string
}) {
  const navigate = useNavigate()
  const [mode, setMode] = useState<DiffMode>("base")
  const [layout, setLayout] = useState<DiffLayout>("unified")
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [annotations, setAnnotations] = useState<LineAnnotation[]>([])
  const [draftTarget, setDraftTarget] = useState<{
    path: string
    line: number
    side: "old" | "new"
    code: string
  } | null>(null)
  const [draftText, setDraftText] = useState("")

  useEffect(() => {
    setAnnotations([])
    setDraftTarget(null)
    setDraftText("")
  }, [worktreeRef])

  const filesQuery = useWorktreeFiles(worktreeRef, mode)
  const files: WorktreeDiffFile[] = filesQuery.data?.files ?? []

  useEffect(() => {
    const first = files[0]
    if (!first) {
      setSelectedPath(null)
      return
    }
    if (!selectedPath || !files.some((f) => f.path === selectedPath)) {
      setSelectedPath(first.path)
    }
  }, [files, selectedPath])

  const diffQuery = useWorktreeDiff(worktreeRef, selectedPath, mode)
  const createRun = useCreateRun()

  const countsByFile = useMemo(() => {
    const map: Record<string, number> = {}
    for (const a of annotations) {
      map[a.path] = (map[a.path] ?? 0) + 1
    }
    return map
  }, [annotations])

  function openComposer(path: string, line: number, side: "old" | "new", code: string) {
    setDraftTarget({ path, line, side, code })
    setDraftText("")
  }

  function saveAnnotation() {
    if (!draftTarget || !draftText.trim()) return
    const next: LineAnnotation = {
      id: `${draftTarget.path}:${draftTarget.side}:${draftTarget.line}:${Date.now()}`,
      path: draftTarget.path,
      line: draftTarget.line,
      side: draftTarget.side,
      code: draftTarget.code,
      comment: draftText.trim(),
    }
    setAnnotations((prev) => [...prev, next])
    setDraftTarget(null)
    setDraftText("")
  }

  function removeAnnotation(id: string) {
    setAnnotations((prev) => prev.filter((a) => a.id !== id))
  }

  async function handleDispatch() {
    if (annotations.length === 0) return
    const prompt = formatAnnotationsPrompt(annotations)
    try {
      if (onDispatchAnnotations) {
        await onDispatchAnnotations(prompt, annotations)
        setAnnotations([])
        return
      }
      const { run_id } = await createRun.mutateAsync({
        command: "adhoc",
        args: [worktreeRef, "--acp", "--prompt", prompt],
        confirm: true,
      })
      setAnnotations([])
      toast.success("Diff review annotations sent to agent", {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  const lineAnnotations = (path: string, line: number | null, side: "old" | "new") =>
    line == null ? [] : annotations.filter((a) => a.path === path && a.line === line && a.side === side)

  const isDrafting = (path: string, line: number | null, side: "old" | "new") =>
    Boolean(
      draftTarget &&
        line != null &&
        draftTarget.path === path &&
        draftTarget.line === line &&
        draftTarget.side === side,
    )

  return (
    <div className={`flex flex-col gap-3 ${className}`} data-testid="workspace-diff-view">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b pb-2">
        <div className="flex items-center gap-1" role="group" aria-label="Diff mode">
          <Button
            type="button"
            size="sm"
            variant={mode === "base" ? "default" : "outline"}
            onClick={() => setMode("base")}
          >
            Base
          </Button>
          <Button
            type="button"
            size="sm"
            variant={mode === "head" ? "default" : "outline"}
            onClick={() => setMode("head")}
          >
            Working
          </Button>
          {filesQuery.data ? (
            <span className="ml-2 text-xs text-muted-foreground">
              {files.length} file{files.length === 1 ? "" : "s"} ·{" "}
              <span className="text-emerald-600 dark:text-emerald-400">+{filesQuery.data.total_additions}</span>{" "}
              <span className="text-red-600 dark:text-red-400">-{filesQuery.data.total_deletions}</span>
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-1" role="group" aria-label="Diff layout">
          <Button
            type="button"
            size="sm"
            variant={layout === "unified" ? "default" : "outline"}
            onClick={() => setLayout("unified")}
          >
            Unified
          </Button>
          <Button
            type="button"
            size="sm"
            variant={layout === "split" ? "default" : "outline"}
            onClick={() => setLayout("split")}
          >
            Split
          </Button>
        </div>
      </div>

      {/* Main content */}
      {filesQuery.isPending ? (
        <div className="py-8 text-center text-sm text-muted-foreground">Loading changed files…</div>
      ) : filesQuery.isError ? (
        <ErrorState error={filesQuery.error} onRetry={() => void filesQuery.refetch()} />
      ) : files.length === 0 ? (
        <EmptyState
          icon={<FileCode2 className="size-8" aria-hidden />}
          title="No changed files"
          description={
            mode === "base"
              ? "This worktree has no changes relative to its base branch."
              : "Working tree is clean relative to HEAD."
          }
        />
      ) : (
        <div className="grid min-h-[320px] grid-cols-1 gap-3 md:grid-cols-[220px_1fr]">
          {/* File tree */}
          <nav
            aria-label="Changed files"
            className="max-h-[540px] space-y-1 overflow-y-auto rounded-md border bg-muted/20 p-1.5"
          >
            {files.map((f) => {
              const active = f.path === selectedPath
              const noteCount = countsByFile[f.path] ?? 0
              return (
                <button
                  key={f.path}
                  type="button"
                  onClick={() => setSelectedPath(f.path)}
                  className={`flex w-full items-center justify-between gap-1.5 rounded px-2 py-1.5 text-left font-mono text-xs transition-colors ${
                    active ? "bg-accent text-accent-foreground font-medium" : "hover:bg-accent/50"
                  }`}
                  title={f.old_path ? `${f.old_path} → ${f.path}` : f.path}
                >
                  <span className="flex min-w-0 items-center gap-1.5">
                    <Badge
                      variant="outline"
                      className={`h-4 px-1 font-mono text-[10px] ${STATUS_BADGE[f.status] ?? ""}`}
                    >
                      {f.status}
                    </Badge>
                    <span className="truncate">{f.path}</span>
                  </span>
                  <span className="flex shrink-0 items-center gap-1 text-[11px]">
                    {noteCount > 0 ? (
                      <Badge variant="secondary" className="h-4 px-1 text-[10px]">
                        {noteCount}
                      </Badge>
                    ) : null}
                    {f.binary ? (
                      <span className="text-muted-foreground">bin</span>
                    ) : (
                      <>
                        {f.additions > 0 ? (
                          <span className="text-emerald-600 dark:text-emerald-400">+{f.additions}</span>
                        ) : null}
                        {f.deletions > 0 ? (
                          <span className="text-red-600 dark:text-red-400">-{f.deletions}</span>
                        ) : null}
                      </>
                    )}
                  </span>
                </button>
              )
            })}
          </nav>

          {/* Diff pane */}
          <div className="min-w-0 overflow-hidden rounded-md border">
            {selectedPath ? (
              <div className="flex items-center justify-between border-b bg-muted/40 px-3 py-1.5 font-mono text-xs">
                <span className="truncate font-medium">{selectedPath}</span>
                {diffQuery.data ? (
                  <span className="shrink-0 text-muted-foreground">
                    +{diffQuery.data.additions} / -{diffQuery.data.deletions}
                  </span>
                ) : null}
              </div>
            ) : null}

            {diffQuery.isPending ? (
              <div className="p-6 text-center text-sm text-muted-foreground">Loading diff…</div>
            ) : diffQuery.isError ? (
              <div className="p-4">
                <ErrorState error={diffQuery.error} onRetry={() => void diffQuery.refetch()} />
              </div>
            ) : diffQuery.data?.binary ? (
              <div className="p-6 text-center text-sm text-muted-foreground">
                Binary file diff is not displayed.
              </div>
            ) : !diffQuery.data || diffQuery.data.hunks.length === 0 ? (
              <div className="p-6 text-center text-sm text-muted-foreground">No textual hunks.</div>
            ) : (
              <div className="max-h-[500px] overflow-auto font-mono text-xs">
                {diffQuery.data.truncated ? (
                  <div className="flex items-center gap-2 border-b bg-amber-500/10 px-3 py-1.5 text-amber-800 dark:text-amber-300">
                    <AlertTriangle className="size-3.5 shrink-0" aria-hidden />
                    <span>Diff truncated due to size cap.</span>
                  </div>
                ) : null}

                {layout === "unified" ? (
                  <table className="w-full border-collapse">
                    <tbody>
                      {diffQuery.data.hunks.map((hunk, hIdx) => (
                        <HunkUnifiedRows
                          key={`${hIdx}-${hunk.header}`}
                          hunk={hunk}
                          path={diffQuery.data!.path}
                          lineAnnotations={lineAnnotations}
                          isDrafting={isDrafting}
                          draftText={draftText}
                          onDraftTextChange={setDraftText}
                          onOpenComposer={openComposer}
                          onSaveAnnotation={saveAnnotation}
                          onCancelDraft={() => setDraftTarget(null)}
                          onRemoveAnnotation={removeAnnotation}
                        />
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <table className="w-full table-fixed border-collapse">
                    <tbody>
                      {diffQuery.data.hunks.map((hunk, hIdx) => (
                        <HunkSplitRows
                          key={`${hIdx}-${hunk.header}`}
                          hunk={hunk}
                          path={diffQuery.data!.path}
                          lineAnnotations={lineAnnotations}
                          isDrafting={isDrafting}
                          draftText={draftText}
                          onDraftTextChange={setDraftText}
                          onOpenComposer={openComposer}
                          onSaveAnnotation={saveAnnotation}
                          onCancelDraft={() => setDraftTarget(null)}
                          onRemoveAnnotation={removeAnnotation}
                        />
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Batch annotation bar */}
      {annotations.length > 0 ? (
        <div
          className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-primary/30 bg-primary/5 px-3 py-2"
          data-testid="annotation-batch-bar"
        >
          <span className="text-xs font-medium">
            {annotations.length} inline comment{annotations.length === 1 ? "" : "s"} staged
          </span>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setAnnotations([])}
            >
              Clear
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={createRun.isPending}
              onClick={() => void handleDispatch()}
            >
              <Send className="mr-1.5 size-3.5" aria-hidden />
              Send to Agent
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  )
}

interface HunkRowsProps {
  hunk: DiffHunk
  path: string
  lineAnnotations: (path: string, line: number | null, side: "old" | "new") => LineAnnotation[]
  isDrafting: (path: string, line: number | null, side: "old" | "new") => boolean
  draftText: string
  onDraftTextChange: (v: string) => void
  onOpenComposer: (path: string, line: number, side: "old" | "new", code: string) => void
  onSaveAnnotation: () => void
  onCancelDraft: () => void
  onRemoveAnnotation: (id: string) => void
}

function lineBg(type: DiffLine["type"]): string {
  if (type === "add") return "bg-emerald-500/10"
  if (type === "del") return "bg-red-500/10"
  return ""
}

function HunkUnifiedRows(props: HunkRowsProps) {
  const { hunk, path } = props
  return (
    <>
      <tr className="bg-muted/50 text-muted-foreground">
        <td colSpan={4} className="px-2 py-1 text-[11px]">
          {hunk.header}
        </td>
      </tr>
      {hunk.lines.map((ln, idx) => {
        const side: "old" | "new" = ln.type === "del" ? "old" : "new"
        const lineNo = side === "old" ? ln.old_line : ln.new_line
        const notes = props.lineAnnotations(path, lineNo, side)
        const drafting = props.isDrafting(path, lineNo, side)
        return (
          <FragmentWithNotes
            key={`${idx}-${ln.old_line}-${ln.new_line}`}
            colSpan={4}
            notes={notes}
            drafting={drafting}
            draftText={props.draftText}
            onDraftTextChange={props.onDraftTextChange}
            onSaveAnnotation={props.onSaveAnnotation}
            onCancelDraft={props.onCancelDraft}
            onRemoveAnnotation={props.onRemoveAnnotation}
          >
            <tr className={`group ${lineBg(ln.type)}`}>
              <td className="w-10 select-none border-r px-1.5 text-right text-muted-foreground">
                {ln.old_line ?? ""}
              </td>
              <td className="w-10 select-none border-r px-1.5 text-right text-muted-foreground">
                {ln.new_line ?? ""}
              </td>
              <td className="w-6 select-none px-1 text-center">
                {lineNo != null && ln.type !== "meta" ? (
                  <button
                    type="button"
                    aria-label={`Comment on ${path} line ${lineNo}`}
                    title={`Add inline comment on line ${lineNo}`}
                    onClick={() => props.onOpenComposer(path, lineNo, side, ln.content)}
                    className="inline-flex size-4 items-center justify-center rounded text-muted-foreground opacity-60 hover:bg-primary/15 hover:text-primary hover:opacity-100 focus:opacity-100"
                  >
                    +
                  </button>
                ) : null}
              </td>
              <td className="whitespace-pre-wrap break-all px-2 py-0.5">
                <span className="mr-1 select-none text-muted-foreground">
                  {ln.type === "add" ? "+" : ln.type === "del" ? "-" : " "}
                </span>
                <HighlightedLine text={ln.content} path={path} />
              </td>
            </tr>
          </FragmentWithNotes>
        )
      })}
    </>
  )
}

function HunkSplitRows(props: HunkRowsProps) {
  const { hunk, path } = props
  const rows = useMemo(() => buildSplitRows(hunk), [hunk])
  return (
    <>
      <tr className="bg-muted/50 text-muted-foreground">
        <td colSpan={4} className="px-2 py-1 text-[11px]">
          {hunk.header}
        </td>
      </tr>
      {rows.map((row, idx) => {
        const rightLine = row.right?.new_line ?? null
        const leftLine = row.left?.old_line ?? null
        const notes = [
          ...props.lineAnnotations(path, leftLine, "old"),
          ...props.lineAnnotations(path, rightLine, "new"),
        ]
        const drafting =
          props.isDrafting(path, leftLine, "old") || props.isDrafting(path, rightLine, "new")
        return (
          <FragmentWithNotes
            key={idx}
            colSpan={4}
            notes={notes}
            drafting={drafting}
            draftText={props.draftText}
            onDraftTextChange={props.onDraftTextChange}
            onSaveAnnotation={props.onSaveAnnotation}
            onCancelDraft={props.onCancelDraft}
            onRemoveAnnotation={props.onRemoveAnnotation}
          >
            <tr>
              <td className={`w-12 select-none border-r px-1 text-right text-muted-foreground ${row.left ? lineBg(row.left.type) : "bg-muted/20"}`}>
                <span className="inline-flex items-center gap-1">
                  {leftLine != null ? (
                    <button
                      type="button"
                      aria-label={`Comment on ${path} old line ${leftLine}`}
                      onClick={() =>
                        props.onOpenComposer(
                          path,
                          leftLine,
                          "old",
                          row.left?.content ?? "",
                        )
                      }
                      className="inline-flex size-4 items-center justify-center rounded text-muted-foreground opacity-60 hover:bg-primary/15 hover:text-primary hover:opacity-100"
                    >
                      +
                    </button>
                  ) : null}
                  <span>{leftLine ?? ""}</span>
                </span>
              </td>
              <td className={`w-1/2 whitespace-pre-wrap break-all border-r px-2 py-0.5 ${row.left ? lineBg(row.left.type) : "bg-muted/20"}`}>
                {row.left ? <HighlightedLine text={row.left.content} path={path} /> : null}
              </td>
              <td className={`w-12 select-none border-r px-1 text-right text-muted-foreground ${row.right ? lineBg(row.right.type) : "bg-muted/20"}`}>
                <span className="inline-flex items-center gap-1">
                  {rightLine != null ? (
                    <button
                      type="button"
                      aria-label={`Comment on ${path} line ${rightLine}`}
                      onClick={() =>
                        props.onOpenComposer(
                          path,
                          rightLine,
                          "new",
                          row.right?.content ?? "",
                        )
                      }
                      className="inline-flex size-4 items-center justify-center rounded text-muted-foreground opacity-60 hover:bg-primary/15 hover:text-primary hover:opacity-100"
                    >
                      +
                    </button>
                  ) : null}
                  <span>{rightLine ?? ""}</span>
                </span>
              </td>
              <td className={`w-1/2 whitespace-pre-wrap break-all px-2 py-0.5 ${row.right ? lineBg(row.right.type) : "bg-muted/20"}`}>
                {row.right ? <HighlightedLine text={row.right.content} path={path} /> : null}
              </td>
            </tr>
          </FragmentWithNotes>
        )
      })}
    </>
  )
}

function FragmentWithNotes({
  children,
  colSpan,
  notes,
  drafting,
  draftText,
  onDraftTextChange,
  onSaveAnnotation,
  onCancelDraft,
  onRemoveAnnotation,
}: {
  children: React.ReactNode
  colSpan: number
  notes: LineAnnotation[]
  drafting: boolean
  draftText: string
  onDraftTextChange: (v: string) => void
  onSaveAnnotation: () => void
  onCancelDraft: () => void
  onRemoveAnnotation: (id: string) => void
}) {
  return (
    <>
      {children}
      {notes.map((n) => (
        <tr key={n.id} className="bg-sky-500/10">
          <td colSpan={colSpan} className="border-y px-3 py-1.5 font-sans text-xs">
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-start gap-1.5">
                <MessageSquarePlus className="mt-0.5 size-3.5 shrink-0 text-sky-600 dark:text-sky-400" aria-hidden />
                <span>{n.comment}</span>
              </div>
              <button
                type="button"
                aria-label={`Remove comment on line ${n.line}`}
                onClick={() => onRemoveAnnotation(n.id)}
                className="text-muted-foreground hover:text-destructive"
              >
                <Trash2 className="size-3.5" aria-hidden />
              </button>
            </div>
          </td>
        </tr>
      ))}
      {drafting ? (
        <tr className="bg-muted/40">
          <td colSpan={colSpan} className="border-y p-2 font-sans">
            <div className="flex flex-col gap-1.5">
              <textarea
                aria-label="Inline line comment"
                rows={2}
                className="w-full rounded border bg-background px-2 py-1 text-xs"
                placeholder="Write a review comment for this line…"
                value={draftText}
                onChange={(e) => onDraftTextChange(e.target.value)}
              />
              <div className="flex justify-end gap-1.5">
                <Button type="button" size="sm" variant="ghost" onClick={onCancelDraft}>
                  Cancel
                </Button>
                <Button
                  type="button"
                  size="sm"
                  disabled={!draftText.trim()}
                  onClick={onSaveAnnotation}
                >
                  Add comment
                </Button>
              </div>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  )
}
