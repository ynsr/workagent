import { useMemo, useState } from "react"
import { Check, ChevronsUpDown } from "lucide-react"
import { Label } from "@/components/ui/label"
import { FieldHelp } from "@/components/FieldHelp"
import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { useRepos } from "@/lib/queries"
import { cn } from "@/lib/utils"

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
  variant = "chips",
}: {
  idPrefix: string
  mainRepo: string
  value: string[]
  onChange: (v: string[]) => void
  /** "dropdown" compacts the picker into a popover (tight step forms). */
  variant?: "chips" | "dropdown"
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
  if (variant === "dropdown") {
    return (
      <RelatedReposDropdown
        idPrefix={idPrefix}
        options={options}
        value={value}
        onToggle={toggle}
      />
    )
  }
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

/** Compact popover multi-select for tight forms (flow template steps). */
function RelatedReposDropdown({
  idPrefix,
  options,
  value,
  onToggle,
}: {
  idPrefix: string
  options: string[]
  value: string[]
  onToggle: (name: string) => void
}) {
  const [open, setOpen] = useState(false)
  const summary = value.length === 0 ? "Select repos…" : value.slice().sort((a, b) => a.localeCompare(b)).join(", ")
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id={`${idPrefix}-related-repos`}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-full justify-between font-normal"
        >
          <span className={cn("truncate", value.length === 0 && "text-muted-foreground/60 italic")}>{summary}</span>
          <ChevronsUpDown aria-hidden className="ml-2 size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="max-h-56 overflow-auto p-1" align="start">
        {options.map((name) => {
          const checked = value.includes(name)
          return (
            <button
              key={name}
              type="button"
              role="option"
              aria-selected={checked}
              onClick={() => onToggle(name)}
              className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left font-mono text-[13px] hover:bg-accent hover:text-accent-foreground"
            >
              <Check aria-hidden className={cn("size-4 shrink-0", checked ? "opacity-100" : "opacity-0")} />
              <span className="truncate">{name}</span>
            </button>
          )
        })}
      </PopoverContent>
    </Popover>
  )
}
