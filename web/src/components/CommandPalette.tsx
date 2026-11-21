import { useEffect, useMemo, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import * as DialogPrimitive from "@radix-ui/react-dialog"
import {
  Compass,
  FolderGit2,
  GitBranch,
  Library,
  Search,
  SquareTerminal,
  Zap,
} from "lucide-react"
import { useHotkeys } from "@/hooks/useHotkeys"
import {
  useActions,
  useFlowTemplates,
  useRepos,
  useRuns,
  useStatusAll,
  useStatusDeleted,
  useStatusInactive,
} from "@/lib/queries"
import { shortId } from "@/lib/format"
import { cn } from "@/lib/utils"

export type PaletteCategory =
  | "Worktrees"
  | "Repos"
  | "Flow Templates"
  | "Actions"
  | "Recent Runs"
  | "Navigation"

export interface PaletteItem {
  id: string
  category: PaletteCategory
  label: string
  subtitle?: string
  badge?: string
  keywords?: string
  onSelect: () => void
}

const NAV_ROUTES = [
  { to: "/", label: "Dashboard", keywords: "home worktrees status board" },
  { to: "/launch", label: "Launch", keywords: "start review sync cleanup" },
  { to: "/messages", label: "Messages", keywords: "mattermost threads" },
  { to: "/message-launch", label: "Message Launch", keywords: "mattermost run" },
  { to: "/flows", label: "Flows", keywords: "pipelines chains" },
  { to: "/runs", label: "Runs", keywords: "history logs transcripts" },
  { to: "/prodtests", label: "Prodtests", keywords: "production verification" },
  { to: "/prodtest-launch", label: "Prodtest Launch", keywords: "plan run" },
  { to: "/flow-templates", label: "Flow Templates", keywords: "templates workflows" },
  { to: "/actions", label: "Actions", keywords: "custom actions prompts" },
  { to: "/repos", label: "Repos", keywords: "repositories git" },
  { to: "/trackers", label: "Trackers", keywords: "jira github gitlab" },
  { to: "/links", label: "Links", keywords: "worktree links" },
  { to: "/doctor", label: "Doctor", keywords: "health diagnostics" },
  { to: "/settings", label: "Settings", keywords: "preferences config theme" },
] as const

const CATEGORY_ORDER: PaletteCategory[] = [
  "Worktrees",
  "Repos",
  "Flow Templates",
  "Actions",
  "Recent Runs",
  "Navigation",
]

function categoryIcon(category: PaletteCategory) {
  switch (category) {
    case "Worktrees":
      return GitBranch
    case "Repos":
      return FolderGit2
    case "Flow Templates":
      return Library
    case "Actions":
      return Zap
    case "Recent Runs":
      return SquareTerminal
    case "Navigation":
      return Compass
  }
}

/** Rank-based filter: exact/prefix label match > substring label > subtitle/keywords/category. */
export function filterPaletteItems(items: PaletteItem[], rawQuery: string): PaletteItem[] {
  const q = rawQuery.trim().toLowerCase()
  if (!q) return items

  const tokens = q.split(/\s+/).filter(Boolean)
  const scored: { item: PaletteItem; score: number }[] = []

  for (const item of items) {
    const label = item.label.toLowerCase()
    const hay = `${label} ${(item.subtitle ?? "").toLowerCase()} ${(item.badge ?? "").toLowerCase()} ${(item.keywords ?? "").toLowerCase()} ${item.category.toLowerCase()}`
    if (!tokens.every((t) => hay.includes(t))) continue

    let score = 0
    if (label === q) score += 100
    else if (label.startsWith(q)) score += 60
    else if (label.includes(q)) score += 30
    else score += 10
    scored.push({ item, score })
  }

  scored.sort((a, b) => b.score - a.score)
  return scored.map((s) => s.item)
}

export interface CommandPaletteProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  // Global Cmd+K / Ctrl+K toggle (works even when typing in an input)
  useHotkeys([
    {
      key: "Mod+K",
      allowInInputs: true,
      handler: () => onOpenChange(!open),
    },
  ])

  if (!open) return null

  return <CommandPaletteContent onOpenChange={onOpenChange} />
}

function CommandPaletteContent({ onOpenChange }: { onOpenChange: (open: boolean) => void }) {
  const navigate = useNavigate()
  const [query, setQuery] = useState("")
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const { data: activeStatus } = useStatusAll()
  const { data: inactiveStatus } = useStatusInactive()
  const { data: deletedStatus } = useStatusDeleted()
  const { data: repos } = useRepos()
  const { data: flowTemplates } = useFlowTemplates()
  const { data: actions } = useActions()
  const { data: runs } = useRuns()

  function go(to: string) {
    onOpenChange(false)
    navigate(to)
  }

  const allItems = useMemo<PaletteItem[]>(() => {
    const out: PaletteItem[] = []
    const seenWorktrees = new Set<string>()

    const addWorktree = (key: string, entry: { branch?: string; repo?: string; pr?: string; deleted_at?: string; active?: number }, statusLabel: string) => {
      if (seenWorktrees.has(key)) return
      seenWorktrees.add(key)
      out.push({
        id: `wt:${key}`,
        category: "Worktrees",
        label: key,
        subtitle: [entry.branch, entry.repo, entry.pr].filter(Boolean).join(" · "),
        badge: statusLabel,
        keywords: `${key} ${entry.branch ?? ""} ${entry.repo ?? ""} ${statusLabel}`,
        onSelect: () => go(`/?q=${encodeURIComponent(key)}`),
      })
    }

    for (const [key, entry] of Object.entries(activeStatus ?? {})) {
      addWorktree(key, entry, "active")
    }
    for (const [key, entry] of Object.entries(inactiveStatus ?? {})) {
      if (entry.active === 0 && !entry.deleted_at) {
        addWorktree(key, entry, "deactivated")
      }
    }
    for (const [key, entry] of Object.entries(deletedStatus ?? {})) {
      if (entry.deleted_at) {
        addWorktree(key, entry, "deleted")
      }
    }

    for (const repo of repos ?? []) {
      out.push({
        id: `repo:${repo.name}`,
        category: "Repos",
        label: repo.name,
        subtitle: repo.path,
        keywords: `${repo.name} ${repo.path} ${repo.default_branch ?? ""}`,
        onSelect: () => go(`/?repo=${encodeURIComponent(repo.name)}`),
      })
    }

    for (const tpl of flowTemplates?.templates ?? []) {
      out.push({
        id: `flow-tpl:${tpl.label}`,
        category: "Flow Templates",
        label: tpl.name || tpl.label,
        subtitle: tpl.label,
        keywords: `${tpl.label} ${tpl.name} ${tpl.category ?? ""}`,
        onSelect: () => go(`/flow-templates`),
      })
    }

    for (const action of actions?.actions ?? []) {
      out.push({
        id: `action:${action.label}`,
        category: "Actions",
        label: action.name || action.label,
        subtitle: action.label,
        keywords: `${action.label} ${action.name} ${action.category ?? ""}`,
        onSelect: () => go(`/actions`),
      })
    }

    const recentRuns = [...(runs ?? [])]
      .sort((a, b) => b.created - a.created)
      .slice(0, 15)
    for (const run of recentRuns) {
      out.push({
        id: `run:${run.id}`,
        category: "Recent Runs",
        label: `${run.command} · ${run.target || shortId(run.id)}`,
        subtitle: shortId(run.id),
        badge: run.state,
        keywords: `${run.id} ${run.command} ${run.target} ${run.state} ${run.worktree}`,
        onSelect: () => go(`/runs/${encodeURIComponent(run.id)}`),
      })
    }

    for (const route of NAV_ROUTES) {
      out.push({
        id: `nav:${route.to}`,
        category: "Navigation",
        label: route.label,
        subtitle: route.to,
        keywords: route.keywords,
        onSelect: () => go(route.to),
      })
    }

    return out
  }, [activeStatus, inactiveStatus, deletedStatus, repos, flowTemplates, actions, runs])

  const filtered = useMemo(() => filterPaletteItems(allItems, query), [allItems, query])

  // Group filtered items preserving CATEGORY_ORDER and assign flatIndex in rendered order
  const { grouped, flatItems } = useMemo(() => {
    const byCat = new Map<PaletteCategory, PaletteItem[]>()
    for (const cat of CATEGORY_ORDER) byCat.set(cat, [])
    for (const item of filtered) {
      byCat.get(item.category)?.push(item)
    }
    const flat: PaletteItem[] = []
    const groups: { category: PaletteCategory; entries: { item: PaletteItem; flatIndex: number }[] }[] = []
    for (const cat of CATEGORY_ORDER) {
      const catItems = byCat.get(cat) ?? []
      if (catItems.length === 0) continue
      const entries = catItems.map((item) => {
        const flatIndex = flat.length
        flat.push(item)
        return { item, flatIndex }
      })
      groups.push({ category: cat, entries })
    }
    return { grouped: groups, flatItems: flat }
  }, [filtered])

  useEffect(() => {
    requestAnimationFrame(() => inputRef.current?.focus())
  }, [])

  useEffect(() => {
    setActiveIndex(0)
  }, [query])

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" || (e.ctrlKey && e.key.toLowerCase() === "n")) {
      e.preventDefault()
      if (flatItems.length > 0) {
        setActiveIndex((cur) => (cur + 1) % flatItems.length)
      }
      return
    }
    if (e.key === "ArrowUp" || (e.ctrlKey && e.key.toLowerCase() === "p")) {
      e.preventDefault()
      if (flatItems.length > 0) {
        setActiveIndex((cur) => (cur - 1 + flatItems.length) % flatItems.length)
      }
      return
    }
    if (e.key === "Enter") {
      e.preventDefault()
      const chosen = flatItems[activeIndex]
      if (chosen) chosen.onSelect()
      return
    }
  }

  const activeItem = flatItems[activeIndex]

  return (
    <DialogPrimitive.Root open onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/50 backdrop-blur-[1px]" />
        <DialogPrimitive.Content
          aria-label="Command Palette"
          className="fixed left-1/2 top-[15%] z-50 w-[min(92vw,38rem)] -translate-x-1/2 overflow-hidden rounded-xl border bg-popover text-popover-foreground shadow-2xl outline-none"
        >
          <DialogPrimitive.Title className="sr-only">Command Palette</DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">
            Search worktrees, repos, flow templates, actions, recent runs, and navigation routes.
          </DialogPrimitive.Description>

          <div className="flex items-center gap-2 border-b px-3.5 py-2.5">
            <Search aria-hidden className="size-4 shrink-0 text-muted-foreground" />
            <input
              ref={inputRef}
              type="text"
              role="combobox"
              aria-expanded={true}
              aria-controls="command-palette-listbox"
              aria-autocomplete="list"
              aria-activedescendant={activeItem ? `palette-item-${activeItem.id}` : undefined}
              placeholder="Jump to worktree, repo, flow, action, run, or page…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
            <kbd className="hidden rounded border bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground sm:inline-block">
              ESC
            </kbd>
          </div>

          <div
            id="command-palette-listbox"
            role="listbox"
            aria-label="Command results"
            className="max-h-[60vh] overflow-y-auto p-2"
          >
            {grouped.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                No matching commands for “{query}”.
              </p>
            ) : (
              grouped.map((group) => {
                const Icon = categoryIcon(group.category)
                return (
                  <div key={group.category} className="mb-2 last:mb-0">
                    <div className="px-2 py-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                      {group.category}
                    </div>
                    <div className="space-y-0.5">
                      {group.entries.map(({ item, flatIndex }) => {
                        const isActive = flatIndex === activeIndex
                        return (
                          <button
                            key={item.id}
                            id={`palette-item-${item.id}`}
                            type="button"
                            role="option"
                            aria-selected={isActive}
                            onMouseEnter={() => setActiveIndex(flatIndex)}
                            onClick={() => item.onSelect()}
                            className={cn(
                              "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
                              isActive
                                ? "bg-accent text-accent-foreground"
                                : "text-foreground hover:bg-accent/60",
                            )}
                          >
                            <Icon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
                            <span className="min-w-0 flex-1 truncate font-medium">
                              {item.label}
                            </span>
                            {item.subtitle ? (
                              <span className="max-w-[40%] truncate font-mono text-xs text-muted-foreground">
                                {item.subtitle}
                              </span>
                            ) : null}
                            {item.badge ? (
                              <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                                {item.badge}
                              </span>
                            ) : null}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                )
              })
            )}
          </div>

          <div className="flex items-center justify-between border-t bg-muted/30 px-3.5 py-2 text-[11px] text-muted-foreground">
            <span>↑↓ navigate · ↵ select · Esc close</span>
            <span>⌘K / Ctrl+K</span>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
