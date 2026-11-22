import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Bot } from "lucide-react"
import { api, type WorktreeWatcherSettings } from "@/lib/api"
import { errorText } from "@/components/StatusFeedback"

type TriState = "inherit" | "on" | "off"

function toTri(v: boolean | null | undefined): TriState {
  if (v === true) return "on"
  if (v === false) return "off"
  return "inherit"
}

function fromTri(v: TriState): boolean | null {
  if (v === "on") return true
  if (v === "off") return false
  return null
}

/** Per-worktree auto-watcher override controls (Inherit / On / Off). */
export function WorktreeWatcherOverrides({ worktreeKey }: { worktreeKey: string }) {
  const [settings, setSettings] = useState<WorktreeWatcherSettings | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let active = true
    api.worktreeWatcher(worktreeKey)
      .then((res) => { if (active) setSettings(res) })
      .catch(() => { /* best-effort; hide if unavailable */ })
    return () => { active = false }
  }, [worktreeKey])

  if (!settings) return null

  async function update(field: "auto_fix_ci" | "auto_fix_reviews", next: TriState) {
    setSaving(true)
    try {
      const updated = await api.setWorktreeWatcher(worktreeKey, { [field]: fromTri(next) })
      setSettings(updated)
      toast.success("Watcher override updated")
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded-lg border bg-muted/30 p-3 text-xs" data-testid="worktree-watcher-overrides">
      <div className="mb-2 flex items-center gap-1.5 font-medium text-foreground">
        <Bot className="size-3.5 text-muted-foreground" aria-hidden />
        <span>Auto-watchers</span>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="flex items-center justify-between gap-2">
          <span className="text-muted-foreground">
            Auto-fix CI ({settings.effective_auto_fix_ci ? "On" : "Off"})
          </span>
          <select
            aria-label="Auto-fix CI override"
            disabled={saving}
            value={toTri(settings.auto_fix_ci)}
            onChange={(e) => void update("auto_fix_ci", e.target.value as TriState)}
            className="rounded border bg-background px-2 py-1 text-xs"
          >
            <option value="inherit">Inherit</option>
            <option value="on">On</option>
            <option value="off">Off</option>
          </select>
        </label>
        <label className="flex items-center justify-between gap-2">
          <span className="text-muted-foreground">
            Auto-fix reviews ({settings.effective_auto_fix_reviews ? "On" : "Off"})
          </span>
          <select
            aria-label="Auto-fix reviews override"
            disabled={saving}
            value={toTri(settings.auto_fix_reviews)}
            onChange={(e) => void update("auto_fix_reviews", e.target.value as TriState)}
            className="rounded border bg-background px-2 py-1 text-xs"
          >
            <option value="inherit">Inherit</option>
            <option value="on">On</option>
            <option value="off">Off</option>
          </select>
        </label>
      </div>
    </div>
  )
}
