import { useEffect, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Monitor, Moon, RotateCcw, Sun } from "lucide-react"
import { HarnessPicker } from "@/components/HarnessPicker"
import { PageHeader } from "@/components/PageHeader"
import { errorText } from "@/components/StatusFeedback"
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
import { queryKeys, useConfig, useCreateRun } from "@/lib/queries"
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
  const qc = useQueryClient()
  const { theme, setTheme } = useTheme()
  const { data: cfg } = useConfig()
  const createRun = useCreateRun()
  const [pending, setPending] = useState<{ value: string; baseline: string | undefined } | null>(null)
  const [pendingModel, setPendingModel] = useState<{ value: string; baseline: string | undefined } | null>(null)
  const [pendingEffort, setPendingEffort] = useState<{ value: string; baseline: string | undefined } | null>(null)
  const optimisticActive = pending !== null && cfg?.default_harness === pending.baseline
  const resolvedHarness = optimisticActive ? pending.value : cfg?.default_harness
  const currentHarness = resolvedHarness && resolvedHarness.length > 0 ? resolvedHarness : "agy"
  const optimisticModelActive = pendingModel !== null && cfg?.default_model === pendingModel.baseline
  const currentModel = (optimisticModelActive ? pendingModel.value : cfg?.default_model) ?? ""
  const optimisticEffortActive = pendingEffort !== null && cfg?.default_effort === pendingEffort.baseline
  const currentEffort = (optimisticEffortActive ? pendingEffort.value : cfg?.default_effort) ?? ""

  useEffect(() => {
    if (pending !== null && cfg?.default_harness !== pending.baseline) {
      setPending(null)
    }
  }, [cfg?.default_harness, pending])

  useEffect(() => {
    if (pendingModel !== null && cfg?.default_model !== pendingModel.baseline) {
      setPendingModel(null)
    }
  }, [cfg?.default_model, pendingModel])

  useEffect(() => {
    if (pendingEffort !== null && cfg?.default_effort !== pendingEffort.baseline) {
      setPendingEffort(null)
    }
  }, [cfg?.default_effort, pendingEffort])

  const [verbose, setVerboseState] = useState(() => getVerbose())
  const [optOuts, setOptOutsState] = useState<Partial<Record<OptOutAction, boolean>>>(
    () => getOptOuts(),
  )

  async function handleDefaultHarness(next: string) {
    if (next === currentHarness) return
    const baseline = cfg?.default_harness
    const modelBaseline = cfg?.default_model
    const effortBaseline = cfg?.default_effort
    setPending({ value: next, baseline })
    if (currentModel) setPendingModel({ value: "", baseline: modelBaseline })
    if (currentEffort) setPendingEffort({ value: "", baseline: effortBaseline })
    try {
      await createRun.mutateAsync({
        command: "config",
        args: ["set", "default-harness", next],
        confirm: false,
      })
      if (currentModel) {
        await createRun.mutateAsync({
          command: "config",
          args: ["set", "default-model", ""],
          confirm: false,
        })
      }
      if (currentEffort) {
        await createRun.mutateAsync({
          command: "config",
          args: ["set", "default-effort", ""],
          confirm: false,
        })
      }
      toast.success(`Default AI harness set to ${next}`)
      void qc.invalidateQueries({ queryKey: queryKeys.config })
    } catch (err) {
      setPending((cur) => (cur?.value === next && cur.baseline === baseline ? null : cur))
      setPendingModel((cur) => (cur?.baseline === modelBaseline ? null : cur))
      setPendingEffort((cur) => (cur?.baseline === effortBaseline ? null : cur))
      toast.error(errorText(err))
    }
  }

  async function handleDefaultModel(next: string) {
    if (next === currentModel) return
    const baseline = cfg?.default_model
    setPendingModel({ value: next, baseline })
    try {
      await createRun.mutateAsync({
        command: "config",
        args: ["set", "default-model", next],
        confirm: false,
      })
      toast.success(next ? `Default AI model set to ${next}` : "Default AI model reset to harness default")
      void qc.invalidateQueries({ queryKey: queryKeys.config })
    } catch (err) {
      setPendingModel((cur) => (cur?.value === next && cur.baseline === baseline ? null : cur))
      toast.error(errorText(err))
    }
  }

  async function handleDefaultEffort(next: string) {
    if (next === currentEffort) return
    const baseline = cfg?.default_effort
    setPendingEffort({ value: next, baseline })
    try {
      await createRun.mutateAsync({
        command: "config",
        args: ["set", "default-effort", next],
        confirm: false,
      })
      toast.success(next ? `Default effort set to ${next}` : "Default effort reset to harness default")
      void qc.invalidateQueries({ queryKey: queryKeys.config })
    } catch (err) {
      setPendingEffort((cur) => (cur?.value === next && cur.baseline === baseline ? null : cur))
      toast.error(errorText(err))
    }
  }

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
          <CardTitle>Default AI Harness</CardTitle>
          <CardDescription>
            Coding agent CLI, model, and effort used when <span className="font-mono text-[13px]">--harness</span>, <span className="font-mono text-[13px]">--model</span>, or <span className="font-mono text-[13px]">--effort</span> is not passed.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <HarnessPicker
            idPrefix="settings-default"
            allowDefault={false}
            harness={currentHarness}
            model={currentModel}
            effort={currentEffort}
            onHarnessChange={(next) => void handleDefaultHarness(next)}
            onModelChange={(next) => void handleDefaultModel(next)}
            onEffortChange={(next) => void handleDefaultEffort(next)}
            label="Default harness"
          />
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
