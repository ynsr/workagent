import { useMemo, useState } from "react"
import { ChevronsUpDown } from "lucide-react"
import { ActionIcon } from "@/components/ActionIcon"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/utils"

/** Curated icon list for the picker (all verified against installed lucide-react). */
export const ACTION_ICONS = [
  "Zap", "Sparkles", "Wand2", "Star", "Gem", "Lightbulb", "Bot", "Brain",
  "FileText", "Files", "FilePlus", "FileSearch", "ScrollText", "ClipboardList", "ClipboardCheck", "BookOpen",
  "GitBranch", "GitPullRequest", "GitMerge", "Split", "Diff", "History", "Repeat",
  "TestTube", "TestTubes", "FlaskConical", "CheckCheck", "ListChecks", "ShieldCheck", "CircleCheck", "BadgeCheck",
  "Rocket", "Play", "Send", "Terminal", "Command", "Settings", "SlidersHorizontal", "Wrench", "Hammer",
  "Search", "Eye", "Radar", "Compass", "Target", "Flag", "Tag", "Gauge", "Timer", "Activity",
  "MessageSquare", "ThumbsUp", "Award", "Trophy", "Package", "Boxes", "Layers", "Workflow", "Network",
  "PenLine", "Bug", "Shield", "Swords", "KeyRound", "Lock", "Database", "Globe", "Braces",
  "Calendar", "Clock", "Bell", "Inbox", "Archive", "Folder", "Kanban", "Copy", "Link",
  "Palette", "Image", "Cpu", "Gift", "Smile", "Ghost",
]

/**
 * Searchable Lucide icon dropdown: a button trigger (icon preview + name)
 * opens a Radix Popover with a filter input + icon grid. Typing an unknown
 * name + Enter commits it as custom text (the backend accepts free text).
 */
export function IconPicker({ value, onChange, id }: {
  value: string
  onChange: (v: string) => void
  id?: string
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState("")

  const query = q.trim()
  const filtered = useMemo(
    () => (query ? ACTION_ICONS.filter((n) => n.toLowerCase().includes(query.toLowerCase())) : ACTION_ICONS),
    [query],
  )
  const known = ACTION_ICONS.includes(query)

  function pick(n: string) {
    onChange(n)
    setOpen(false)
  }

  function commitCustom() {
    if (!query) return
    onChange(query)
    setOpen(false)
  }

  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (o) setQ("") }}>
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-full justify-between font-normal"
        >
          <span className={value ? "flex min-w-0 items-center gap-2 truncate" : "flex min-w-0 items-center gap-2 truncate text-muted-foreground/60 italic"}>
            {value ? <ActionIcon name={value} className="size-4 shrink-0" /> : null}
            <span className="truncate">{value || "Zap"}</span>
          </span>
          <ChevronsUpDown aria-hidden className="ml-2 size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="p-1" align="start">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && query && !known) {
              e.preventDefault()
              commitCustom()
            }
          }}
          placeholder="Type to filter icons…"
          autoComplete="off"
          spellCheck={false}
        />
        <div className="max-h-56 overflow-auto py-1">
          {query && !known ? (
            <button
              type="button"
              onClick={commitCustom}
              className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent hover:text-accent-foreground"
            >
              <ActionIcon name={query} className="size-4 shrink-0" />
              <span className="truncate">Use &ldquo;{query}&rdquo;</span>
            </button>
          ) : null}
          {filtered.length === 0 ? (
            <p className="px-2 py-1.5 text-sm text-muted-foreground">No matches.</p>
          ) : null}
          {filtered.map((n) => (
            <button
              key={n}
              type="button"
              role="option"
              aria-selected={n === value}
              onClick={() => pick(n)}
              className={cn(
                "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent hover:text-accent-foreground",
                n === value && "font-medium",
              )}
            >
              <ActionIcon name={n} className="size-4 shrink-0" />
              <span className="truncate">{n}</span>
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  )
}
