import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import { format } from 'date-fns'
import { runsApi } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { SchemaDriftBadge } from '@/components/shared/SchemaDriftBadge'
import { ApiErrorMessage } from '@/components/shared/ApiErrorMessage'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import { LogViewer } from '@/components/runs/LogViewer'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { toast } from '@/hooks/use-toast'

export function RunDetailPage() {
  const { id } = useParams<{ id: string }>()

  const { data: run, isLoading, error } = useQuery({
    queryKey: queryKeys.runs.detail(id!),
    queryFn: () => runsApi.get(id!).then((r) => r.data),
    enabled: !!id,
    refetchInterval: (data) => {
      const status = data?.state?.data?.status
      return status === 'running' || status === 'queued' ? 3000 : false
    },
    refetchIntervalInBackground: false,
  })

  const cancelMutation = useMutation({
    mutationFn: () => runsApi.cancel(id!),
    onSuccess: () => toast({ title: 'Run cancelled' }),
    onError: () => toast({ title: 'Failed to cancel run', variant: 'destructive' }),
  })

  if (isLoading) return <div className="flex justify-center py-12"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" /></div>
  if (error || !run) return <ApiErrorMessage error={error} />

  const isActive = run.status === 'running' || run.status === 'queued'

  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader
        title={`Run ${run.id.slice(0, 8)}…`}
        breadcrumb={<Link to="/runs" className="hover:underline">Runs</Link>}
        action={
          isActive ? (
            <ConfirmDialog
              trigger={<Button variant="outline">Cancel</Button>}
              title="Cancel this run?"
              description="This will attempt to stop the running job."
              confirmLabel="Cancel Run"
              destructive
              onConfirm={() => cancelMutation.mutate()}
            />
          ) : undefined
        }
      />

      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <CardTitle>Status</CardTitle>
            <StatusBadge status={run.status} />
            {run.schema_changed && <SchemaDriftBadge />}
          </div>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-2 text-sm">
          <span className="text-muted-foreground">Triggered by</span><span className="capitalize">{run.triggered_by}</span>
          <span className="text-muted-foreground">Started</span><span>{run.started_at ? format(new Date(run.started_at), 'PPp') : '—'}</span>
          <span className="text-muted-foreground">Finished</span><span>{run.finished_at ? format(new Date(run.finished_at), 'PPp') : '—'}</span>
          <span className="text-muted-foreground">Rows read</span><span>{run.rows_read?.toLocaleString() ?? '—'}</span>
          <span className="text-muted-foreground">Rows written</span><span>{run.rows_written?.toLocaleString() ?? '—'}</span>
          {run.error && <>
            <span className="text-muted-foreground">Error</span>
            <span className="text-destructive text-xs break-all">{run.error}</span>
          </>}
          {run.pipeline_id && <>
            <span className="text-muted-foreground">Pipeline</span>
            <Link to={`/pipelines/${run.pipeline_id}`} className="text-primary hover:underline font-mono">
              {run.pipeline_id.slice(0, 8)}…
            </Link>
          </>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Logs</CardTitle></CardHeader>
        <CardContent>
          <LogViewer runId={run.id} status={run.status} />
        </CardContent>
      </Card>
    </div>
  )
}
