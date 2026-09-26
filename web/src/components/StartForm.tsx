import { useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"
import { SearchableSelect } from "@/components/SearchableSelect"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { errorText } from "@/components/StatusFeedback"
import { useRepos } from "@/lib/queries"
import { api } from "@/lib/api"
import { INITIAL } from "@/lib/launchConfig"
import type { LaunchForm } from "@/lib/launchConfig"
import { CheckRow, FieldHelp } from "@/components/FieldHelp"

export interface StartFormValue extends LaunchForm {}

/** Shared Start-form fields (Launch page + candidate Start dialog). */
export function StartFormFields({
  form,
  onChange,
  idPrefix,
  issuesNonce,
}: {
  form: StartFormValue
  onChange: <K extends keyof StartFormValue>(key: K, value: StartFormValue[K]) => void
  idPrefix: string
  /** Remount key for the issue dropdown (Launch refresh button). */
  issuesNonce?: number
}) {
  const { data: repos } = useRepos()
  const repoNames = useMemo(() => (repos ?? []).map((r) => r.name), [repos])
  const [candidates, setCandidates] = useState<string[]>([])
  const refValue = form.ref.trim()
  // Latest repo value for the timeout below (avoids an effect dep loop).
  const repoRef = useRef(form.repo)
  repoRef.current = form.repo

  // Repo is mandatory: the debounced lookup fills the field whenever the
  // backend returns a repo (linked worktree wins, else single linked repo);
  // the user can always override. Never blank a manual pick.
  useEffect(() => {
    const ref = refValue
    if (!ref) {
      setCandidates([])
      return
    }
    let live = true
    const t = setTimeout(() => {
      api
        .defaultRepo(ref)
        .then((r) => {
          if (!live) return
          setCandidates(r.repos ?? [])
          if (r.repo && !repoRef.current.trim()) onChange("repo", r.repo as StartFormValue["repo"])
        })
        .catch(() => {
          if (!live) return
        })
    }, 250)
    return () => {
      live = false
      clearTimeout(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refValue])

  const options = useMemo(() => {
    const out: string[] = []
    for (const n of [...candidates, ...repoNames]) {
      if (!out.includes(n)) out.push(n)
    }
    return out
  }, [candidates, repoNames])

  return (
    <>
      <div className="grid gap-2">
        <Label htmlFor={`${idPrefix}-ref`}>Issue ref</Label>
        {issuesNonce !== undefined ? (
          <>
            <SearchableSelect
              key={issuesNonce}
              id={`${idPrefix}-ref`}
              value={form.ref}
              options={[]}
              onChange={(v) => onChange("ref", v.trim())}
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
            id={`${idPrefix}-ref`}
            value={form.ref}
            onChange={(e) => onChange("ref", e.target.value)}
            placeholder="IPG-932, OWNER/REPO#22, or https://…/issues/22"
            autoComplete="off"
            spellCheck={false}
          />
        )}
      </div>

      <div className="grid gap-5 sm:grid-cols-2">
        <div className="grid gap-2">
          <Label htmlFor={`${idPrefix}-repo`}>
            <FieldHelp label="Repository" flag="--repo" description="Registered name, local path, or clone URL. Required — prefilled from the ref when the backend knows it." />
          </Label>
          <SearchableSelect
            value={form.repo}
            options={options}
            onChange={(v) => onChange("repo", v)}
            placeholder="Select a repo…"
            allowCustom
          />
          {candidates.length > 1 ? (
            <p className="text-xs text-muted-foreground">
              {candidates.length} repos linked to this tracker — pick one.
            </p>
          ) : null}
        </div>
        <div className="grid gap-2">
          <Label htmlFor={`${idPrefix}-depth`}>
            <FieldHelp label="Clone depth" flag="--depth" description="Git clone depth for the new worktree (default 7)." />
          </Label>
          <Input
            id={`${idPrefix}-depth`}
            type="number"
            min={1}
            value={form.depth}
            onChange={(e) => onChange("depth", e.target.value)}
            placeholder="7"
          />
        </div>
      </div>

      <div className="grid gap-5 sm:grid-cols-2">
        <div className="grid gap-2">
          <Label htmlFor={`${idPrefix}-base`}>
            <FieldHelp label="Base branch" flag="--base" description="Base branch for the new worktree (default: repo default)." />
          </Label>
          <Input
            id={`${idPrefix}-base`}
            value={form.base}
            onChange={(e) => onChange("base", e.target.value)}
            placeholder="repo default"
            spellCheck={false}
          />
        </div>
      </div>

      <div className="flex flex-wrap gap-x-6 gap-y-3">
        <CheckRow
          id={`${idPrefix}-launch`}
          checked={form.launch}
          onChange={(v) => onChange("launch", v)}
          label="Run agent now"
          flag="--launch"
          description="Run the agent now (default: print the command and hand over the worktree)."
        />
      </div>
    </>
  )
}

/** Build `start` args from the shared form (Launch page + dialog agree). */
export function buildStartArgs(form: StartFormValue): string[] {
  const ref = form.ref.trim()
  const repo = form.repo.trim()
  const args = [ref, "--no-tty"]
  if (repo) args.push("--repo", repo)
  if (form.depth.trim()) args.push("--depth", form.depth.trim())
  if (form.base.trim()) args.push("--base", form.base.trim())
  if (form.launch) args.push("--launch")
  return args
}

/** Confirm-dialog detail rows for a start run. */
export function startFlagList(form: StartFormValue): string {
  const repo = form.repo.trim()
  return [
    "headless (--no-tty)",
    repo ? `--repo ${repo}` : "repo: pick a repo",
    `--depth ${form.depth.trim() || "7"}`,
    form.base.trim() ? `--base ${form.base.trim()}` : "base: repo default",
    form.launch ? "--launch (run the agent now)" : "preview (print command, no run)",
  ].join(", ")
}

export function useStartForm(initial?: Partial<StartFormValue>) {
  const [form, setForm] = useState<StartFormValue>({ ...INITIAL, ...initial })
  function update<K extends keyof StartFormValue>(key: K, value: StartFormValue[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }
  async function prefillRepo(ref: string, field: keyof StartFormValue = "repo") {
    if (!ref.trim()) return
    try {
      const r = await api.defaultRepo(ref.trim())
      if (r.repo) setForm((f) => (f[field] ? f : { ...f, [field]: r.repo }))
    } catch (err) {
      toast.error(errorText(err))
    }
  }
  return { form, setForm, update, prefillRepo }
}
