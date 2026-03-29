import { Badge } from '@/components/ui/badge'
import type { JobRun } from '@/lib/api'

type Status = JobRun['status']

const statusConfig: Record<Status, { label: string; variant: 'default' | 'success' | 'destructive' | 'warning' | 'running' | 'secondary' }> = {
  queued: { label: 'Queued', variant: 'secondary' },
  running: { label: 'Running', variant: 'running' },
  success: { label: 'Success', variant: 'success' },
  failed: { label: 'Failed', variant: 'destructive' },
  partial_success: { label: 'Partial', variant: 'warning' },
}

export function StatusBadge({ status }: { status: Status }) {
  const { label, variant } = statusConfig[status] ?? { label: status, variant: 'secondary' as const }
  return <Badge variant={variant}>{label}</Badge>
}
