import { Badge } from '@/components/ui/badge'
import type { JobRun } from '@/lib/api'

type Status = JobRun['status']

const statusConfig: Record<
  Status,
  { label: string; variant: 'default' | 'success' | 'destructive' | 'warning' | 'running' | 'secondary'; dot: string }
> = {
  queued: { label: 'Queued', variant: 'secondary', dot: 'bg-muted-foreground/70' },
  running: { label: 'Running', variant: 'running', dot: 'bg-sky-500 animate-pulse' },
  success: { label: 'Success', variant: 'success', dot: 'bg-emerald-500' },
  failed: { label: 'Failed', variant: 'destructive', dot: 'bg-rose-500' },
  partial_success: { label: 'Partial', variant: 'warning', dot: 'bg-amber-500' },
}

export function StatusBadge({ status }: { status: Status }) {
  const { label, variant, dot } = statusConfig[status] ?? {
    label: status,
    variant: 'secondary' as const,
    dot: 'bg-muted-foreground',
  }
  return (
    <Badge variant={variant}>
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden="true" />
      {label}
    </Badge>
  )
}
