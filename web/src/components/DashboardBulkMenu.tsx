import { useState } from "react"
import { ChevronDown, Copy, Download } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"

/**
 * Dashboard top-bar dropdown: the seven bulk/export buttons (Adhoc,
 * Sync all, Review all, Fix all PR comments, Cleanup merged, JSON, CSV) in one
 * menu. Labels stay selection-aware ("Sync (2)" / "Sync all"); handlers
 * are owned by the Dashboard page. Refresh PR, Prodtest, Close released,
 * and the worktree-paths toggle stay as standalone top-bar controls.
 */
export interface DashboardBulkMenuProps {
  /** "Adhoc (2)" when rows selected, else "Adhoc prompt…". */
  adhocLabel: string
  adhocTitle: string
  onAdhoc: () => void
  /** "Sync (2)" when rows selected, else "Sync all". */
  syncLabel: string
  syncTitle: string
  onSync: () => void
  /** "Review (2)" when rows selected, else "Review all". */
  reviewLabel: string
  reviewTitle: string
  onReview: () => void
  /** "Fix (2)" when rows selected, else "Fix all PR comments". */
  fixLabel: string
  fixTitle: string
  onFix: () => void
  cleanupLabel: string
  cleanupDisabled?: boolean
  cleanupTitle?: string
  onCleanup: () => void
  onCopyJson: () => void
  onDownloadCsv: () => void
}

export function DashboardBulkMenu(props: DashboardBulkMenuProps) {
  const [open, setOpen] = useState(false)

  function run(fn: () => void) {
    setOpen(false)
    fn()
  }

  const items: {
    key: string
    icon: React.ReactNode
    label: string
    title?: string
    disabled?: boolean
    onClick: () => void
  }[] = [
    { key: "adhoc", icon: null, label: props.adhocLabel, title: props.adhocTitle, onClick: () => run(props.onAdhoc) },
    { key: "sync", icon: null, label: props.syncLabel, title: props.syncTitle, onClick: () => run(props.onSync) },
    { key: "review", icon: null, label: props.reviewLabel, title: props.reviewTitle, onClick: () => run(props.onReview) },
    { key: "fix", icon: null, label: props.fixLabel, title: props.fixTitle, onClick: () => run(props.onFix) },
    {
      key: "cleanup",
      icon: null,
      label: props.cleanupLabel,
      title: props.cleanupTitle,
      disabled: props.cleanupDisabled,
      onClick: () => run(props.onCleanup),
    },
    { key: "json", icon: <Copy aria-hidden className="size-4 shrink-0" />, label: "Copy JSON", onClick: () => run(props.onCopyJson) },
    { key: "csv", icon: <Download aria-hidden className="size-4 shrink-0" />, label: "Download CSV", onClick: () => run(props.onDownloadCsv) },
  ]

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" aria-label="Bulk actions">
          Bulk actions
          <ChevronDown aria-hidden className="size-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-56 p-1" align="end">
        {items.map((item) => (
          <button
            key={item.key}
            type="button"
            disabled={item.disabled}
            onClick={item.onClick}
            title={item.title}
            className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
          >
            {item.icon}
            <span className="min-w-0 flex-1 truncate">{item.label}</span>
          </button>
        ))}
      </PopoverContent>
    </Popover>
  )
}
