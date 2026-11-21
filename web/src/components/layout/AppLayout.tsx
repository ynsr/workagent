import { useState } from "react"
import { NavLink, Outlet } from "react-router-dom"
import {
  ChevronsLeft,
  ChevronsRight,
  FlaskConical,
  FolderGit2,
  GitBranch,
  LayoutDashboard,
  Library,
  MessageSquare,
  Search,
  Settings2,
  ShieldAlert,
  SquareTerminal,
} from "lucide-react"
import { CommandPalette } from "@/components/CommandPalette"
import { RunWatcher } from "@/components/RunWatcher"
import { useInfo } from "@/lib/queries"
import { cn } from "@/lib/utils"

const NAV_TOP_GROUPS = [
  [
    { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
    { to: "/messages", label: "Messages", icon: MessageSquare },
    { to: "/runs", label: "Runs & Flows", icon: SquareTerminal },
    { to: "/prodtests", label: "Prodtests", icon: FlaskConical },
    { to: "/library", label: "Automation Library", icon: Library },
  ],
] as const

const NAV_BOTTOM_GROUPS = [
  [
    { to: "/repos", label: "Repos & Trackers", icon: FolderGit2 },
    { to: "/settings", label: "Settings", icon: Settings2 },
  ],
] as const


function NavLinkContent({ label, icon: Icon }: { label: string; icon: typeof LayoutDashboard }) {
  return (
    <>
      <Icon aria-hidden className="size-4 shrink-0" />
      <span>{label}</span>
    </>
  )
}
type SidebarNavItem = { to: string; label: string; icon: typeof LayoutDashboard; end?: boolean }

function SidebarNavLink({ item, collapsed }: { item: SidebarNavItem; collapsed: boolean }) {
  return (
    <NavLink
      to={item.to}
      end={"end" in item ? item.end : false}
      title={collapsed ? item.label : undefined}
      className={({ isActive }) =>
        cn(
          "flex min-h-11 items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors",
          collapsed && "justify-center px-0",
          isActive
            ? "bg-sidebar-accent text-sidebar-accent-foreground"
            : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
        )
      }
    >
      {collapsed ? (
        <item.icon aria-hidden className="size-4 shrink-0" />
      ) : (
        <NavLinkContent label={item.label} icon={item.icon} />
      )}
    </NavLink>
  )
}

function SidebarGroup({
  group,
  collapsed,
  separated,
}: {
  group: readonly SidebarNavItem[]
  collapsed: boolean
  separated: boolean
}) {
  return (
    <div className={cn(separated && "border-t border-sidebar-border/70 pt-3")}>
      <div className="space-y-1">
        {group.map((item) => (
          <SidebarNavLink key={item.to} item={item} collapsed={collapsed} />
        ))}
      </div>
    </div>
  )
}

const SIDEBAR_KEY = "workagent-sidebar-collapsed";

function Sidebar({
  collapsed,
  onToggle,
  onOpenPalette,
}: {
  collapsed: boolean;
  onToggle: () => void;
  onOpenPalette: () => void;
}) {
  return (
    <aside className={cn("fixed inset-y-0 left-0 z-40 hidden flex-col border-r bg-sidebar text-sidebar-foreground lg:flex", collapsed ? "w-16" : "w-64")}>
      <div className={cn("flex items-center gap-2.5 py-5", collapsed ? "justify-center px-3" : "px-5")}>
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <GitBranch aria-hidden className="size-5" />
        </span>
        {collapsed ? null : (
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold leading-tight">workagent</p>
            <p className="truncate text-xs text-muted-foreground">serve</p>
          </div>
        )}
      </div>
      <div className={cn("px-3 pb-2", collapsed && "flex justify-center")}>
        <button
          type="button"
          onClick={onOpenPalette}
          aria-label="Search (⌘K)"
          title="Search (⌘K)"
          className={cn(
            "flex items-center gap-2 rounded-md border bg-background/60 text-xs text-muted-foreground transition-colors hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
            collapsed ? "size-9 justify-center" : "h-9 w-full px-2.5",
          )}
        >
          <Search aria-hidden className="size-3.5 shrink-0" />
          {collapsed ? null : (
            <>
              <span className="flex-1 text-left">Search…</span>
              <kbd className="rounded border bg-muted px-1.5 py-0.5 font-mono text-[10px]">
                ⌘K
              </kbd>
            </>
          )}
        </button>
      </div>
      <nav className="flex-1 space-y-3 overflow-y-auto px-3 py-2" aria-label="Primary">
        {NAV_TOP_GROUPS.map((group, index) => (
          <SidebarGroup key={group[0].to} group={group} collapsed={collapsed} separated={index > 0} />
        ))}
      </nav>
      <nav className="space-y-3 px-3 pb-2" aria-label="Secondary">
        {NAV_BOTTOM_GROUPS.map((group, index) => (
          <SidebarGroup key={group[0].to} group={group} collapsed={collapsed} separated={index > 0} />
        ))}
      </nav>
      {collapsed ? null : <SidebarMeta />}
      <div className={cn("border-t p-3", collapsed && "flex justify-center")}>
        <button
          type="button"
          onClick={onToggle}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className="flex size-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground"
        >
          {collapsed ? <ChevronsRight aria-hidden className="size-4" /> : <ChevronsLeft aria-hidden className="size-4" />}
        </button>
      </div>
    </aside>
  )
}

function SidebarMeta() {
  const { data: info } = useInfo()
  return (
    <div className="border-t px-5 py-4 text-xs text-muted-foreground">
      {info ? (
        <p className="font-mono">v{info.version}</p>
      ) : (
        <p className="font-mono">v—</p>
      )}
      {info?.network_exposed ? (
        <p className="mt-1 flex items-center gap-1.5 text-amber-600 dark:text-amber-400">
          <ShieldAlert aria-hidden className="size-3.5" /> network-exposed
        </p>
      ) : null}
    </div>
  )
}
/** Mobile bottom bar: curated 6-item subset — one full grid row (all
 * NAV_ITEMS would spill onto a third row and cover page content). */
const BOTTOM_NAV_ITEMS = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/messages", label: "Messages", icon: MessageSquare },
  { to: "/runs", label: "Runs", icon: SquareTerminal },
  { to: "/prodtests", label: "Prodtests", icon: FlaskConical },
  { to: "/library", label: "Library", icon: Library },
  { to: "/repos", label: "Repos", icon: FolderGit2 },
] as const


function BottomNav() {
  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-40 border-t bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80 lg:hidden"
    >
      <div className="grid grid-cols-6 pb-[env(safe-area-inset-bottom)]">
        {BOTTOM_NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={"end" in item ? item.end : false}
            className={({ isActive }) =>
              cn(
                "flex min-h-14 min-w-0 flex-col items-center justify-center gap-1 px-0.5 text-[10px] font-medium",
                isActive ? "text-primary" : "text-muted-foreground",
              )
            }
          >
            <item.icon aria-hidden className="size-5" />
            <span className="max-w-full truncate">{item.label}</span>
          </NavLink>
        ))}
      </div>
    </nav>
  )
}

function MobileHeader({ onOpenPalette }: { onOpenPalette: () => void }) {
  return (
    <header className="sticky top-0 z-30 flex items-center justify-between gap-2.5 border-b bg-background/95 px-4 py-3 backdrop-blur supports-[backdrop-filter]:bg-background/80 lg:hidden">
      <div className="flex items-center gap-2.5">
        <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <GitBranch aria-hidden className="size-4" />
        </span>
        <span className="text-sm font-semibold">workagent serve</span>
      </div>
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={onOpenPalette}
          aria-label="Search (⌘K)"
          className="flex h-8 items-center gap-1.5 rounded-md border bg-background px-2.5 text-xs text-muted-foreground hover:text-foreground"
        >
          <Search aria-hidden className="size-3.5" />
          <span>Search</span>
        </button>
        <NavLink
          to="/settings"
          aria-label="Settings"
          title="Settings"
          className={({ isActive }) =>
            cn(
              "flex size-9 items-center justify-center rounded-md transition-colors",
              isActive
                ? "bg-sidebar-accent text-sidebar-accent-foreground"
                : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
            )
          }
        >
          <Settings2 aria-hidden className="size-4" />
        </NavLink>
      </div>
    </header>
  )
}

function NetworkBanner() {
  const { data: info } = useInfo()
  if (!info?.network_exposed) return null
  return (
    <div
      role="alert"
      className="flex items-center gap-2 border-b border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-800 dark:text-amber-200"
    >
      <ShieldAlert aria-hidden className="size-4 shrink-0" />
      <span>
        This server is exposed to the network ({info.host}:{info.port}) — anyone
        who can reach it can launch commands and read your worktrees.
      </span>
    </div>
  )
}

function Footer() {
  const { data: info } = useInfo()
  return (
    <footer className="border-t px-4 py-3 text-xs text-muted-foreground lg:px-8">
      workagent serve{info ? ` v${info.version}` : ""} · {info ? `${info.host}:${info.port}` : "connecting…"} ·
      web UI
    </footer>
  )
}

export function AppLayout() {
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_KEY) === "1";
    } catch {
      return false;
    }
  });
  function toggle() {
    setCollapsed((cur) => {
      try {
        localStorage.setItem(SIDEBAR_KEY, cur ? "0" : "1");
      } catch {
        /* storage unavailable — state still applies for the session */
      }
      return !cur;
    });
  }
  return (
    <div className="min-h-svh bg-background">
      <RunWatcher />
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      <Sidebar collapsed={collapsed} onToggle={toggle} onOpenPalette={() => setPaletteOpen(true)} />
      <div className={cn("flex min-h-svh flex-col", collapsed ? "lg:pl-16" : "lg:pl-64")}>
        <MobileHeader onOpenPalette={() => setPaletteOpen(true)} />
        <NetworkBanner />
        <main className="w-full max-w-none flex-1 min-w-0 overflow-x-clip px-4 pt-6 pb-28 lg:px-8 lg:pb-8">
          <Outlet />
        </main>
        <Footer />
      </div>
      <BottomNav />
    </div>
  )
}
