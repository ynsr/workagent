/* eslint-disable react-refresh/only-export-components -- constants/helpers exported beside components (shadcn convention) */
import type { ReactNode } from "react"
import { AlertCircle, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { ApiError } from "@/lib/api"
import { cn } from "@/lib/utils"

export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    return error.status === 0
      ? error.message
      : `${error.code} (${error.status}): ${error.message}`
  }
  if (error instanceof Error) return error.message
  return "Unknown error"
}

export function TableSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-3" aria-busy="true" aria-live="polite">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-4">
          <Skeleton className="h-5 w-32" />
          <Skeleton className="hidden h-5 flex-1 sm:block" />
          <Skeleton className="hidden h-5 w-16 md:block" />
          <Skeleton className="h-5 w-20" />
        </div>
      ))}
    </div>
  )
}

export function ErrorState({
  error,
  onRetry,
  className,
}: {
  error: unknown
  onRetry?: () => void
  className?: string
}) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-6 py-10 text-center",
        className,
      )}
    >
      <AlertCircle className="size-6 text-destructive" aria-hidden />
      <p className="max-w-prose text-sm text-muted-foreground">
        {errorText(error)}
      </p>
      {onRetry ? (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw aria-hidden /> Retry
        </Button>
      ) : null}
    </div>
  )
}

export function EmptyState({
  icon,
  title,
  description,
  children,
}: {
  icon?: ReactNode
  title: string
  description?: string
  children?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed px-6 py-12 text-center">
      {icon ? <div className="text-muted-foreground">{icon}</div> : null}
      <p className="font-medium">{title}</p>
      {description ? (
        <p className="max-w-prose text-sm text-muted-foreground">{description}</p>
      ) : null}
      {children ? <div className="flex flex-wrap justify-center gap-2 pt-1">{children}</div> : null}
    </div>
  )
}
