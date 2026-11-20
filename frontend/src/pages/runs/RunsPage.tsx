import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Activity } from 'lucide-react'
import { format } from 'date-fns'
import { runsApi } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { SchemaDriftBadge } from '@/components/shared/SchemaDriftBadge'
import { EmptyState } from '@/components/shared/EmptyState'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Card, CardContent } from '@/components/ui/card'

const STATUS_OPTIONS = ['queued', 'running', 'success', 'failed', 'partial_success'] as const

export function RunsPage() {
  const [statusFilter, setStatusFilter] = useState('all')
  const params = { limit: 50, ...(statusFilter !== 'all' ? { status: statusFilter } : {}) }

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.runs.list(params),
    queryFn: () => runsApi.list(params).then((r) => r.data),
    refetchInterval: 10_000,
    refetchIntervalInBackground: false,
  })

  const runs = data ?? []

  return (
    <div>
      <PageHeader
        title="Runs"
        description="Global run history"
        action={
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-40">
              <SelectValue placeholder="All statuses" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              {STATUS_OPTIONS.map((s) => (
                <SelectItem key={s} value={s}>{s}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      />

      {isLoading ? (
        <div className="flex justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
        </div>
      ) : runs.length === 0 ? (
        <EmptyState icon={Activity} title="No runs yet" description="Runs appear here when pipelines are triggered." />
      ) : (
        <Card className="overflow-hidden">
          <CardContent className="p-0">
            <div className="divide-y divide-border/60">
              {runs.map((run) => (
                <div
                  key={run.id}
                  className="flex items-center justify-between px-5 py-3.5 hover:bg-foreground/[0.025] transition-colors"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <Link
                        to={`/runs/${run.id}`}
                        className="text-xs font-medium text-foreground hover:underline underline-offset-4"
                      >
                        {run.id.slice(0, 8)}…
                      </Link>
                      {run.pipeline_id && (
                        <Link
                          to={`/pipelines/${run.pipeline_id}`}
                          className="text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                        >
                          · pipeline
                        </Link>
                      )}
                    </div>
                    <div className="text-[11px] text-muted-foreground mt-0.5 tabular-nums">
                      {run.triggered_by} · {run.created_at ? format(new Date(run.created_at), 'MMM d, HH:mm:ss') : '—'}
                      {run.rows_read != null && <> · {run.rows_read.toLocaleString()} rows</>}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {run.schema_changed && <SchemaDriftBadge />}
                    <StatusBadge status={run.status} />
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
