import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { Rocket, ShieldAlert } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useConfirm } from "@/lib/confirm"
import { errorText } from "@/components/StatusFeedback"
import { useCreateRun, useRepos } from "@/lib/queries"
import { cn } from "@/lib/utils"

const AUTO_REPO = "__auto__"
const DEFAULT_HARNESS = "__default__"

type Mode = "start" | "review"

interface LaunchForm {
  ref: string
  repo: string
  depth: string
  base: string
  harness: string
  dryRun: boolean
  json: boolean
}

const INITIAL: LaunchForm = {
  ref: "",
  repo: AUTO_REPO,
  depth: "7",
  base: "",
  harness: DEFAULT_HARNESS,
  dryRun: false,
  json: false,
}

export function Launch() {
  const navigate = useNavigate()
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const { data: repos } = useRepos()
  const [mode, setMode] = useState<Mode>("start")
  const [form, setForm] = useState<LaunchForm>(INITIAL)
  const [submitting, setSubmitting] = useState(false)

  function update<K extends keyof LaunchForm>(key: K, value: LaunchForm[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  function switchMode(next: Mode) {
    setMode(next)
    setForm((f) => ({ ...f, base: "" }))
  }

  const refValue = form.ref.trim()
  const repoValue = form.repo === AUTO_REPO ? undefined : form.repo
  const harnessValue = form.harness === DEFAULT_HARNESS ? undefined : form.harness

  function buildArgs(): string[] {
    const args = [refValue, "--no-tty"]
    if (repoValue) args.push("--repo", repoValue)
    if (form.depth.trim()) args.push("--depth", form.depth.trim())
    if (mode === "start" && form.base.trim()) args.push("--base", form.base.trim())
    if (harnessValue) args.push("--harness", harnessValue)
    if (form.dryRun) args.push("--dry-run")
    if (form.json) args.push("--json")
    return args
  }

  const flagList = [
    "headless (--no-tty)",
    repoValue ? `--repo ${repoValue}` : "repo: registry default",
    `--depth ${form.depth.trim() || "7"}`,
    mode === "start" && form.base.trim() ? `--base ${form.base.trim()}` : "base: repo default",
    harnessValue ? `--harness ${harnessValue}` : "harness: configured default",
    form.dryRun ? "--dry-run" : null,
    form.json ? "--json" : null,
  ].filter((v): v is string => v !== null)

  async function handleSubmit() {
    if (!refValue) return
    const ok = await confirm({
      action: mode,
      title: mode === "start" ? "Launch start" : "Launch review",
      description:
        mode === "start"
          ? "Creates a worktree from the issue and launches the coding agent."
          : "Creates a worktree from the PR/MR and launches a review agent.",
      warning:
        "The agent runs headless with auto-approve (--no-tty): it can commit, push and open MRs/PRs without further prompts. The server appends --yes.",
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
      toast.success(`${mode === "start" ? "Start" : "Review"} launched`, {
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
        description="Start an agent from an issue ref/URL, or review a PR/MR ref/URL. Runs headless as a child process of harness serve."
        actions={
          <div
            role="tablist"
            aria-label="Launch mode"
            className="inline-flex rounded-lg border p-1"
          >
            {(["start", "review"] as const).map((m) => (
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
                {m === "start" ? "Start" : "Review"}
              </button>
            ))}
          </div>
        }
      />

      <Card className="mx-auto max-w-2xl">
        <CardHeader>
          <CardTitle>
            {mode === "start" ? "Start from an issue" : "Review a PR/MR"}
          </CardTitle>
          <CardDescription>
            {mode === "start"
              ? "Issue key, OWNER/REPO#22, or a full issue URL."
              : "PR/MR URL, OWNER/REPO#33, or a session ref (key/branch/worktree)."}
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-5">
          <div className="grid gap-2">
            <Label htmlFor="launch-ref">
              {mode === "start" ? "Issue ref" : "PR/MR ref"}
            </Label>
            <Input
              id="launch-ref"
              value={form.ref}
              onChange={(e) => update("ref", e.target.value)}
              placeholder={
                mode === "start"
                  ? "IPG-932, OWNER/REPO#22, or https://…/issues/22"
                  : "https://…/pull/33, OWNER/REPO#33, or jira:IPG-929"
              }
              autoComplete="off"
              spellCheck={false}
            />
          </div>

          <div className="grid gap-5 sm:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="launch-repo">Repo</Label>
              <Select
                value={form.repo}
                onValueChange={(v) => update("repo", v)}
              >
                <SelectTrigger id="launch-repo" className="w-full" aria-label="Repo">
                  <SelectValue placeholder="Registry default" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={AUTO_REPO}>Registry default (auto)</SelectItem>
                  {(repos ?? []).map((r) => (
                    <SelectItem key={r.name} value={r.name}>
                      {r.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                --repo accepts a registered name; "auto" lets the harness pick.
              </p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="launch-depth">Clone depth</Label>
              <Input
                id="launch-depth"
                type="number"
                min={1}
                value={form.depth}
                onChange={(e) => update("depth", e.target.value)}
                placeholder="7"
              />
            </div>
          </div>

          {mode === "start" ? (
            <div className="grid gap-5 sm:grid-cols-2">
              <div className="grid gap-2">
                <Label htmlFor="launch-base">Base branch</Label>
                <Input
                  id="launch-base"
                  value={form.base}
                  onChange={(e) => update("base", e.target.value)}
                  placeholder="repo default"
                  spellCheck={false}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="launch-harness">Harness</Label>
                <Select
                  value={form.harness}
                  onValueChange={(v) => update("harness", v)}
                >
                  <SelectTrigger id="launch-harness" className="w-full" aria-label="Harness">
                    <SelectValue placeholder="Configured default" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={DEFAULT_HARNESS}>Configured default</SelectItem>
                    <SelectItem value="omp">omp</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          ) : null}

          <div className="flex flex-wrap gap-x-6 gap-y-3">
            <div className="flex items-center gap-2">
              <Checkbox
                id="launch-dry"
                checked={form.dryRun}
                onCheckedChange={(v) => update("dryRun", v === true)}
              />
              <Label htmlFor="launch-dry" className="font-normal">
                <span className="font-mono text-[13px]">--dry-run</span> — print the plan without acting
              </Label>
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="launch-json"
                checked={form.json}
                onCheckedChange={(v) => update("json", v === true)}
              />
              <Label htmlFor="launch-json" className="font-normal">
                <span className="font-mono text-[13px]">--json</span> — JSON output in the run log
              </Label>
            </div>
          </div>

          <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2.5 text-sm text-amber-800 dark:text-amber-200">
            <p className="flex items-start gap-2">
              <ShieldAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
              <span>
                Headless auto-approve: the web server always runs{" "}
                <span className="font-mono text-[13px]">--no-tty</span> (
                <span className="font-mono text-[13px]">omp -p --auto-approve</span>
                ), so the agent can commit, push and open MRs/PRs on its own.
              </span>
            </p>
          </div>

          <div className="flex justify-end gap-2">
            <Button
              type="button"
              onClick={() => void handleSubmit()}
              disabled={!refValue || submitting}
            >
              <Rocket aria-hidden />
              {submitting ? "Launching…" : `Launch ${mode}`}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
