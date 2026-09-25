import { useState } from "react"
import { toast } from "sonner"
import { Monitor, Moon, RotateCcw, Sun } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
  OPTOUT_ACTIONS,
  OPTOUT_LABELS,
  clearOptOuts,
  getOptOuts,
  getVerbose,
  setOptOut,
  setVerbose,
  type OptOutAction,
} from "@/lib/settings"
import { useTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"

const THEME_OPTIONS = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
] as const
export function Settings() {
  const { theme, setTheme } = useTheme()
  const [verbose, setVerboseState] = useState(() => getVerbose())
  const [optOuts, setOptOutsState] = useState<Partial<Record<OptOutAction, boolean>>>(
    () => getOptOuts(),
  )

  function handleVerbose(next: boolean) {
    setVerboseState(next)
    setVerbose(next)
    toast.success(next ? "Verbose runs on (-v)" : "Verbose runs off")
  }

  function handleOptOut(action: OptOutAction, value: boolean) {
    setOptOut(action, value)
    setOptOutsState((prev) => ({ ...prev, [action]: value }))
  }

  function handleResetOptOuts() {
    clearOptOuts()
    setOptOutsState({})
    toast.success("All confirmation opt-outs cleared")
  }

  return (
    <div className="mx-auto grid max-w-3xl gap-6">
      <PageHeader
        title="Settings"
        description="Theme, run verbosity, and confirmation opt-outs — all stored locally in this browser."
      />

      <Card>
        <CardHeader>
          <CardTitle>Theme</CardTitle>
          <CardDescription>
            Follows the system preference by default; your choice is stored per browser.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div role="radiogroup" aria-label="Theme" className="flex gap-2">
            {THEME_OPTIONS.map(({ value, label, icon: Icon }) => (
              <button
                key={value}
                role="radio"
                aria-checked={theme === value}
                onClick={() => setTheme(value)}
                className={cn(
                  "flex min-h-11 flex-1 items-center justify-center gap-2 rounded-lg border px-4 text-sm font-medium transition-colors",
                  theme === value
                    ? "border-primary bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
              >
                <Icon aria-hidden className="size-4" />
                {label}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Verbose runs</CardTitle>
          <CardDescription>
            Prepends <span className="font-mono text-[13px]">-v</span> to every run
            started from the web UI (shows the exact commands in the run log).
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between rounded-md border px-3 py-2">
            <Label htmlFor="verbose-switch" className="font-normal">
              Pass <span className="font-mono text-[13px]">-v</span> to workagent commands
            </Label>
            <Switch
              checked={verbose}
              onCheckedChange={handleVerbose}
              aria-label="Verbose runs"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Confirmation opt-outs</CardTitle>
          <CardDescription>
            Actions you told the app not to re-confirm ("Don't ask again for this
            action"). Force runs always confirm, regardless of these.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          {OPTOUT_ACTIONS.map((action) => (
            <div
              key={action}
              className="flex items-center justify-between rounded-md border px-3 py-2"
            >
              <Label htmlFor={`optout-${action}`} className="font-normal">
                {OPTOUT_LABELS[action]}
              </Label>
              <Switch
                checked={optOuts[action] === true}
                onCheckedChange={(v) => handleOptOut(action, v)}
                aria-label={`Ask again for ${OPTOUT_LABELS[action]}`}
              />
            </div>
          ))}
          <div className="flex justify-end">
            <Button
              variant="outline"
              size="sm"
              onClick={handleResetOptOuts}
              disabled={Object.values(optOuts).every((v) => v !== true)}
            >
              <RotateCcw aria-hidden /> Reset all
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
