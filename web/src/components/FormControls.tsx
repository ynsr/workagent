import { useEffect, useRef, useState } from "react"
import { CircleHelp, Sparkles } from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { errorText } from "@/components/StatusFeedback"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Tooltip } from "@/components/ui/tooltip"
import { api } from "@/lib/api"
import { useCreateRun } from "@/lib/queries"

/** Two/three-option segmented control (the "split button" style shared by
 * the Action / Flow Template forms: run-mode Preview|Headless|Terminal and
 * category worktree|message). Single form per idea: one control, one idea. */
export function SegmentedControl<T extends string>({ idPrefix, label, hint, options, value, onChange }: {
  idPrefix: string
  label: string
  hint?: string
  options: readonly { value: T; label: string; hint?: string }[]
  value: T
  onChange: (v: T) => void
}) {
  const cleanHint = hint?.replace(/^—\s*/, "").trim()
  return (
    <div className="grid gap-1.5">
      <div className="flex items-center gap-1">
        <Label id={`${idPrefix}-label`}>{label}</Label>
        {cleanHint ? (
          <Tooltip label={cleanHint}>
            <span
              role="img"
              tabIndex={0}
              className="inline-flex cursor-help items-center rounded-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50"
              aria-label={`${label}: ${cleanHint}`}
            >
              <CircleHelp aria-hidden className="size-3.5" />
            </span>
          </Tooltip>
        ) : null}
      </div>
      <div
        role="radiogroup"
        aria-labelledby={`${idPrefix}-label`}
        className="flex w-fit gap-0.5 rounded-md bg-secondary p-0.5 text-[13px]"
      >
        {options.map((opt) => (
          <button
            key={opt.value}
            type="button"
            id={`${idPrefix}-${opt.value}`}
            role="radio"
            aria-checked={value === opt.value}
            title={opt.hint}
            onClick={() => onChange(opt.value)}
            className={
              value === opt.value
                ? "rounded-[5px] bg-background px-2.5 py-1 font-medium text-foreground shadow-xs"
                : "rounded-[5px] bg-transparent px-2.5 py-1 text-muted-foreground hover:text-foreground"
            }
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  )
}

/** AI-suggest icon button (issue #62): launches ONE bare judge-style
 * harness session (`action|flow template suggest-icon … --json`) as a run,
 * polls it to completion, and parses the suggested icon name from the run
 * log lines (the output is `{"icon": "Rocket"}` on stdout).
 * Polling stays client-local: `useCreateRun`'s ~30 s invalidate window
 * covers config-speed commands, but suggest-icon runs a full harness
 * session (~2 min ceiling), so this polls the run detail itself. */
export function SuggestIconButton({ command, args, onSuggest, disabled }: {
  command: "action" | "flow"
  args: string[]
  onSuggest: (icon: string) => void
  disabled?: boolean
}) {
  const createRun = useCreateRun()
  const [busy, setBusy] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  useEffect(() => () => {
    clearTimeout(timer.current)
  }, [])

  async function poll(runId: string, deadline: number) {
    try {
      const run = await api.run(runId)
      const icon = extractIcon(run.lines?.map((l) => l.text).join("\n") ?? "")
      if (run.state === "running" && Date.now() < deadline) {
        timer.current = setTimeout(() => void poll(runId, deadline), 1500)
        return
      }
      setBusy(false)
      if (run.state === "succeeded" && icon) {
        onSuggest(icon)
        toast.success(`Suggested icon: ${icon}`)
      } else if (run.state === "running") {
        toast.error("Icon suggestion timed out — pick one manually")
      } else {
        toast.error(run.state === "needs_input"
          ? "Icon suggestion needs input — check the Runs page log"
          : "Icon suggestion failed — pick one manually")
      }
    } catch (err) {
      if (Date.now() < deadline) {
        timer.current = setTimeout(() => void poll(runId, deadline), 1500)
        return
      }
      setBusy(false)
      toast.error(errorText(err))
    }
  }

  async function handleClick() {
    if (busy || disabled) return
    setBusy(true)
    try {
      const { run_id } = await createRun.mutateAsync({ command, args })
      void poll(run_id, Date.now() + 150_000)
    } catch (err) {
      setBusy(false)
      toast.error(errorText(err))
    }
  }

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={() => void handleClick()}
      disabled={busy || disabled}
      title="Ask the AI harness for a Lucide icon name (one cheap judge-style session)"
      className={cn(busy && "opacity-70")}
    >
      <Sparkles aria-hidden className={cn("size-4", busy && "animate-pulse")} />
      {busy ? "Asking AI…" : "Suggest icon"}
    </Button>
  )
}

/** Last `{"icon": "Name"}` JSON object in the log wins; falls back to the
 * last bare identifier token (the CLI prints one JSON line, but a chatty
 * harness may add prose around it). */
export function extractIcon(log: string): string | null {
  let found: string | null = null
  for (const line of log.split("\n")) {
    const m = line.match(/\{\s*"icon"\s*:\s*"([A-Za-z0-9_-]+)"\s*\}/)
    if (m) {
      found = m[1] ?? null
      continue
    }
    const t = line.trim().replace(/^["'`]+|["'`]+$/g, "")
    if (/^[A-Za-z][A-Za-z0-9_-]*$/.test(t) && t.length <= 40) found = t
  }
  return found
}
