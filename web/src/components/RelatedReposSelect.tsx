import { useMemo } from "react"
import { Label } from "@/components/ui/label"
import { FieldHelp } from "@/components/FieldHelp"
import { useRepos } from "@/lib/queries"

/** Multi-select for `start --related-repo` (repeatable flag).
 *
 * Own file per the repo's component-size rule (no new controls inside
 * StartForm.tsx). Options are every registered repo except the main one
 * picked in the Start form; the parent emits one `--related-repo <name>`
 * per checked repo.
 */
export function RelatedReposSelect({
  idPrefix,
  mainRepo,
  value,
  onChange,
}: {
  idPrefix: string
  mainRepo: string
  value: string[]
  onChange: (v: string[]) => void
}) {
  const { data: repos } = useRepos()
  const options = useMemo(() => {
    const main = mainRepo.trim().toLowerCase()
    return (repos ?? [])
      .map((r) => r.name)
      .filter((n) => n.toLowerCase() !== main)
      .sort((a, b) => a.localeCompare(b))
  }, [repos, mainRepo])

  function toggle(name: string) {
    onChange(value.includes(name) ? value.filter((v) => v !== name) : [...value, name])
  }

  if (options.length === 0) return null
  return (
    <div className="grid gap-2">
      <Label id={`${idPrefix}-related-repos`}>
        <FieldHelp
          label="Related repos"
          flag="--related-repo"
          description="Optional extra checkouts: one child worktree per repo on the same branch, grouped under the main worktree."
        />
      </Label>
      <div
        role="group"
        aria-labelledby={`${idPrefix}-related-repos`}
        className="flex flex-wrap gap-2"
      >
        {options.map((name) => {
          const checked = value.includes(name)
          return (
            <button
              key={name}
              type="button"
              role="checkbox"
              aria-checked={checked}
              title={checked ? `remove ${name}` : `add ${name} (--related-repo ${name})`}
              onClick={() => toggle(name)}
              className={checked
                ? "rounded-md border border-primary bg-primary/10 px-2.5 py-1 text-sm font-medium"
                : "rounded-md border px-2.5 py-1 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"}
            >
              {checked ? `✓ ${name}` : name}
            </button>
          )
        })}
      </div>
    </div>
  )
}
