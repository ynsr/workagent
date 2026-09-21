import { NavLink, Outlet } from "react-router-dom"
import {
  FolderGit2,
  GitBranch,
  LayoutDashboard,
  Link2,
  Rocket,
  Settings2,
  ShieldAlert,
  SquareTerminal,
  Stethoscope,
} from "lucide-react"
import { RunWatcher } from "@/components/RunWatcher"
import { useInfo } from "@/lib/queries"
import { cn } from "@/lib/utils"

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/launch", label: "Launch", icon: Rocket },
  { to: "/repos", label: "Repos", icon: FolderGit2 },
  { to: "/links", label: "Links", icon: Link2 },
  { to: "/doctor", label: "Doctor", icon: Stethoscope },
  { to: "/runs", label: "Runs", icon: SquareTerminal },
  { to: "/settings", label: "Settings", icon: Settings2 },
] as const

function NavLinkContent({ label, icon: Icon }: { label: string; icon: typeof Rocket }) {
  return (
    <>
      <Icon aria-hidden className="size-4 shrink-0" />
      <span>{label}</span>
    </>
  )
}

function Sidebar() {
  return (
    <aside className="fixed inset-y-0 left-0 z-40 hidden w-64 flex-col border-r bg-sidebar text-sidebar-foreground lg:flex">
      <div className="flex items-center gap-2.5 px-5 py-5">
        <span className="flex size-9 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <GitBranch aria-hidden className="size-5" />
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold leading-tight">harness</p>
          <p className="truncate text-xs text-muted-foreground">serve</p>
        </div>
      </div>
      <nav className="flex-1 space-y-1 px-3 py-2" aria-label="Primary">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={"end" in item ? item.end : false}
            className={({ isActive }) =>
              cn(
                "flex min-h-11 items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors",
                isActive
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
              )
            }
          >
            <NavLinkContent label={item.label} icon={item.icon} />
          </NavLink>
        ))}
      </nav>
      <SidebarMeta />
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

function BottomNav() {
  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-40 border-t bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80 lg:hidden"
    >
      <div className="grid grid-cols-7 pb-[env(safe-area-inset-bottom)]">
        {NAV_ITEMS.map((item) => (
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

function MobileHeader() {
  return (
    <header className="sticky top-0 z-30 flex items-center gap-2.5 border-b bg-background/95 px-4 py-3 backdrop-blur supports-[backdrop-filter]:bg-background/80 lg:hidden">
      <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
        <GitBranch aria-hidden className="size-4" />
      </span>
      <span className="text-sm font-semibold">harness serve</span>
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
      harness serve{info ? ` v${info.version}` : ""} · {info ? `${info.host}:${info.port}` : "connecting…"} ·
      web UI
    </footer>
  )
}

export function AppLayout() {
  return (
    <div className="min-h-svh bg-background">
      <RunWatcher />
      <Sidebar />
      <div className="flex min-h-svh flex-col lg:pl-64">
        <MobileHeader />
        <NetworkBanner />
        <main className="w-full max-w-none flex-1 min-w-0 px-4 pt-6 pb-28 lg:px-8 lg:pb-8">
          <Outlet />
        </main>
        <Footer />
      </div>
      <BottomNav />
    </div>
  )
}
