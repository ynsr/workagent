/* eslint-disable react-refresh/only-export-components -- constants/helpers exported beside components (shadcn convention) */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react"
import { useQuery } from "@tanstack/react-query"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type WorktreeDetail } from "./api"
import { isOptedOut, setOptOut, type OptOutAction } from "./settings"

export interface ConfirmDetailRow {
  label: string
  value?: string
  href?: string
  mono?: boolean
}

export interface ConfirmOptions {
  /** Opt-out bucket; omit when the action must always confirm (e.g. force). */
  action?: OptOutAction | null
  title: string
  description?: string
  confirmLabel?: string
  destructive?: boolean
  /** Force mode: always shows the dialog, never offers the opt-out checkbox. */
  force?: boolean
  /** Extra rows shown from caller-provided data. */
  details?: ConfirmDetailRow[]
  /** Worktree ref — the dialog fetches /api/status?ref= and shows its fields. */
  ref?: string
  /** Prominent warning line (e.g. headless auto-approve). */
  warning?: string
  /** Extra controls (checkboxes for --merge/--dry-run/--json/…) above the actions. */
  extras?: ReactNode
}

interface Pending extends ConfirmOptions {
  resolve: (ok: boolean) => void
}

interface ConfirmContextValue {
  confirm: (options: ConfirmOptions) => Promise<boolean>
}

const ConfirmContext = createContext<ConfirmContextValue | null>(null)

function rowsFromDetail(detail: WorktreeDetail | undefined): ConfirmDetailRow[] {
  if (!detail) return []
  const pr = detail.pr_detail
  const rows: ConfirmDetailRow[] = [{ label: "Worktree", value: detail.key }]
  if (detail.issue_url) {
    rows.push({ label: "Issue", value: detail.issue_url, href: detail.issue_url })
  }
  if (pr) {
    rows.push({
      label: "PR",
      value: `#${pr.number} ${pr.state} — ${pr.title}`,
      href: pr.url,
    })
  } else if (detail.pr) {
    rows.push({ label: "PR", value: detail.pr })
  }
  if (detail.worktree) {
    rows.push({ label: "Worktree", value: detail.worktree, mono: true })
  }
  if (detail.branch) {
    rows.push({ label: "Branch", value: detail.branch, mono: true })
  }
  if (detail.base_branch) {
    rows.push({ label: "Base branch", value: detail.base_branch, mono: true })
  }
  return rows
}

function DetailRows({ rows, isLoading }: { rows: ConfirmDetailRow[]; isLoading: boolean }) {
  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-4 w-1/2" />
      </div>
    )
  }
  if (rows.length === 0) return null
  return (
    <div className="space-y-1.5 rounded-md border bg-muted/40 p-3 text-sm">
      {rows.map((row) => (
        <div key={row.label} className="flex min-w-0 items-baseline gap-2">
          <span className="w-20 shrink-0 text-xs font-medium text-muted-foreground">
            {row.label}
          </span>
          {row.href ? (
            <a
              href={row.href}
              target="_blank"
              rel="noreferrer"
              className="min-w-0 truncate text-primary underline-offset-2 hover:underline"
            >
              {row.value}
            </a>
          ) : (
            <span
              className={
                row.mono
                  ? "min-w-0 truncate font-mono text-[13px]"
                  : "min-w-0 truncate"
              }
            >
              {row.value}
            </span>
          )}
        </div>
      ))}
    </div>
  )
}

function ConfirmDialog({
  pending,
  onClose,
}: {
  pending: Pending | null
  onClose: (ok: boolean) => void
}) {
  const [dontAsk, setDontAsk] = useState(false)
  const ref = pending?.ref
  const detail = useQuery({
    queryKey: ["status-detail", ref ?? null],
    queryFn: () => api.statusDetail(ref!),
    enabled: Boolean(ref),
    staleTime: 5_000,
  })

  useEffect(() => {
    setDontAsk(false)
  }, [pending])

  if (!pending) return null

  const showOptOut = Boolean(pending.action) && !pending.force
  const isLoading = Boolean(ref) && detail.isPending
  const rows = [...(pending.details ?? []), ...rowsFromDetail(detail.data)]

  function handleOpenChange(open: boolean) {
    if (!open) onClose(false)
  }

  function handleConfirm() {
    if (dontAsk && pending?.action && !pending?.force) {
      setOptOut(pending.action, true)
    }
    onClose(true)
  }

  return (
    <AlertDialog open onOpenChange={handleOpenChange}>
      <AlertDialogContent className="max-w-2xl">
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            {pending.destructive ? (
              <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-full bg-destructive" />
            ) : null}
            {pending.title}
          </AlertDialogTitle>
          {pending.description ? (
            <AlertDialogDescription>{pending.description}</AlertDialogDescription>
          ) : null}
          {pending.warning ? (
            <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
              {pending.warning}
            </p>
          ) : null}
          <div className="space-y-3">
            <DetailRows rows={rows} isLoading={isLoading} />
            {pending.extras ? <div>{pending.extras}</div> : null}
            {showOptOut ? (
              <div className="flex items-center gap-2">
                <Checkbox
                  id="confirm-optout"
                  checked={dontAsk}
                  onCheckedChange={(v) => setDontAsk(v === true)}
                />
                <Label
                  htmlFor="confirm-optout"
                  className="text-sm font-normal text-muted-foreground"
                >
                  Don&rsquo;t ask again for this action (reset in Settings)
                </Label>
              </div>
            ) : null}
          </div>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            autoFocus
            onClick={handleConfirm}
            className={
              pending.destructive
                ? "bg-destructive text-white hover:bg-destructive/90"
                : undefined
            }
          >
            {pending.confirmLabel ?? "Confirm"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null)
  const pendingRef = useRef<Pending | null>(null)

  const confirm = useCallback((options: ConfirmOptions): Promise<boolean> => {
    // An opted-out (non-force) action resolves immediately.
    if (options.action && !options.force && isOptedOut(options.action)) {
      return Promise.resolve(true)
    }
    return new Promise<boolean>((resolve) => {
      pendingRef.current?.resolve(false)
      const next: Pending = { ...options, resolve }
      pendingRef.current = next
      setPending(next)
    })
  }, [])

  const close = useCallback((ok: boolean) => {
    pendingRef.current?.resolve(ok)
    pendingRef.current = null
    setPending(null)
  }, [])

  return (
    <ConfirmContext.Provider value={{ confirm }}>
      {children}
      <ConfirmDialog pending={pending} onClose={close} />
    </ConfirmContext.Provider>
  )
}

export function useConfirm(): (options: ConfirmOptions) => Promise<boolean> {
  const ctx = useContext(ConfirmContext)
  if (!ctx) throw new Error("useConfirm must be used within a ConfirmProvider")
  return ctx.confirm
}
