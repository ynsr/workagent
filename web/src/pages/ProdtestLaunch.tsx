import { useEffect, useMemo, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import { PageHeader } from "@/components/PageHeader"
import { ProdtestActionSelect, RunModeSelect } from "@/components/StartForm"
import { SearchableSelect } from "@/components/SearchableSelect"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { FieldHelp } from "@/components/FieldHelp"
import { Label } from "@/components/ui/label"
import { Input } from "@/components/ui/input"
import { useConfirm } from "@/lib/confirm"
import { errorText } from "@/components/StatusFeedback"
import { useCreateRun, useLinks, useRepos } from "@/lib/queries"
import { api } from "@/lib/api"
import type { LaunchForm, ProdtestAction, RunMode } from "@/lib/launchConfig"
import { extraPromptArgs, runModeArgs } from "@/lib/launchConfig"
import { loadLaunchPrefs, saveLaunchPrefs } from "@/lib/launchPrefs"


/** Standalone Prod Test launch (moved off the Launch page's Prodtest tab, issue #50). */
export function ProdtestLaunch() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const { data: links } = useLinks()
  const { data: repos } = useRepos()
  const refParam = params.get("ref") ?? ""
  const actionParam = params.get("prodtestAction")

  const [form, setForm] = useState<Pick<LaunchForm, "ref" | "repo" | "runMode" | "prodtestAction" | "extraPrompt">>(() => {
    const stored = loadLaunchPrefs()
    return {
      ref: refParam,
      repo: "",
      runMode: stored.prodtest.runMode as RunMode,
      prodtestAction: (actionParam === "plan" ? "plan" : stored.prodtest.prodtestAction) as ProdtestAction,
      extraPrompt: "",
    }
  })
  const [submitting, setSubmitting] = useState(false)
  const [prefillChecked, setPrefillChecked] = useState(false)

  function update<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  useEffect(() => {
    saveLaunchPrefs({ ...loadLaunchPrefs(), prodtest: { runMode: form.runMode, prodtestAction: form.prodtestAction } })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.runMode, form.prodtestAction])

  useEffect(() => {
    if (prefillChecked || !refParam || !links) return
    setPrefillChecked(true)
    const entry = links.worktrees[refParam]
    if (entry?.repo) {
      setForm((f) => (f.repo ? f : { ...f, repo: entry.repo as string }))
    } else {
      api.defaultRepo(refParam).then(
        (r) => {
          if (r.repo) setForm((f) => (f.repo ? f : { ...f, repo: r.repo }))
        },
        () => {},
      )
    }
  }, [prefillChecked, refParam, links])

  const refValue = form.ref.trim()
  const repoNames = useMemo(() => (repos ?? []).map((r) => r.name), [repos])
  const isRunAll = form.prodtestAction === "run" && !refValue

  function buildArgs(): string[] {
    const args = isRunAll ? ["run", "--all"] : [form.prodtestAction, refValue]
    args.push(...runModeArgs(form.runMode))
    if (form.extraPrompt.trim()) args.push(...extraPromptArgs(form))
    return args
  }

  async function handleSubmit() {
    if (!refValue && !isRunAll) return
    const ok = await confirm({
      action: "prodtest",
      title: isRunAll ? "Run all prodtests" : `Launch prodtest ${form.prodtestAction}`,
      description: isRunAll
        ? "Runs every stored prodtest plan with a recorded release (non-TTY)."
        : form.prodtestAction === "plan"
          ? "Generates the prodtest plan for the issue."
          : "Runs the stored prodtest plan in an ephemeral read-only checkout at its release SHA (non-TTY).",
      confirmLabel: "Launch",
      details: [
        { label: "Ref", value: refValue || "(all)", mono: true },
        { label: "Flags", value: buildArgs().join(" ") },
      ],
    })
    if (!ok) return
    setSubmitting(true)
    try {
      const { run_id } = await createRun.mutateAsync({ command: "prodtest", args: buildArgs(), confirm: true })
      toast.success("Prodtest launched", { action: { label: "View", onClick: () => navigate(`/runs/${run_id}`) } })
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div>
      <PageHeader
        title="Prodtest Launch"
        description="Plan or run a production test for an issue ref — decoupled from the worktree dev flow."
        actions={null}
      />
      <Card className="mx-auto max-w-2xl">
        <CardHeader>
          <CardTitle>Plan or run the prodtest</CardTitle>
          <CardDescription>Issue key, OWNER/REPO#22, or a worktree ref — generate the plan or run it at its release SHA. Empty ref + Run = run --all.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-5">
          <div className="grid gap-2">
            <Label htmlFor="ptl-ref">Issue ref (empty = run --all)</Label>
            <Input
              id="ptl-ref"
              value={form.ref}
              onChange={(e) => update("ref", e.target.value)}
              placeholder="IPG-932, OWNER/REPO#22, or jira:IPG-932@proj"
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="ptl-repo">
              <FieldHelp label="Repository" flag="--repo" description="Registered name, local path, or clone URL. Optional — the CLI resolves from the linked worktree or tracker." />
            </Label>
            <SearchableSelect
              value={form.repo}
              options={repoNames}
              onChange={(v) => update("repo", v)}
              placeholder="Select a repo… (optional)"
              allowCustom
            />
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-3">
            <RunModeSelect idPrefix="ptl" value={form.runMode} onChange={(v) => update("runMode", v)} />
            <ProdtestActionSelect idPrefix="ptl" value={form.prodtestAction} onChange={(v) => update("prodtestAction", v)} />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="ptl-extra-prompt">
              <FieldHelp label="Extra prompt" flag="--extra-prompt" description="Appended to the agent prompt (free text)." />
            </Label>
            <Input
              id="ptl-extra-prompt"
              value={form.extraPrompt}
              onChange={(e) => update("extraPrompt", e.target.value)}
              placeholder="optional extra instructions"
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <div className="flex gap-2">
            <Button onClick={() => void handleSubmit()} disabled={submitting || (!refValue && !isRunAll)}>
              {submitting ? "Launching…" : isRunAll ? "Run all prodtests" : `${form.prodtestAction === "plan" ? "Plan" : "Run"} prodtest`}
            </Button>
            <Button variant="outline" onClick={() => navigate("/prodtests")}>
              All prodtests
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
