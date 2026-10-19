import * as icons from "lucide-react"
import { Zap } from "lucide-react"

/** Lucide icon by stored name; unknown/blank names fall back to Zap. */
export function ActionIcon({ name, className }: { name?: string | null; className?: string }) {
  const key = (name ?? "").trim()
  const Cmp = (key && (icons as Record<string, unknown>)[key] as React.ComponentType<{ className?: string; "aria-hidden"?: boolean | "true" | "false" }> | undefined) || Zap
  return <Cmp className={className} aria-hidden />
}
