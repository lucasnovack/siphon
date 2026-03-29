import { useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Edit, Play } from 'lucide-react'
import { format } from 'date-fns'
import { pipelinesApi } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { SchemaDriftBadge } from '@/components/shared/SchemaDriftBadge'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import { ApiErrorMessage } from '@/components/shared/ApiErrorMessage'
import { CronInput } from '@/components/shared/CronInput'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { toast } from '@/hooks/use-toast'

export function PipelineDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [cronExpr, setCronExpr] = useState('')
  const [showScheduleForm, setShowScheduleForm] = useState(false)

  const { data: pipeline, isLoading, error } = useQuery({
    queryKey: queryKeys.pipelines.detail(id!),
    queryFn: () => pipelinesApi.get(id!).then((r) => r.data),
    enabled: !!id,
  })

  const { data: runs } = useQuery({
    queryKey: queryKeys.pipelines.runs(id!, 1, 10),
    queryFn: () => pipelinesApi.runs(id!, 1, 10).then((r) => r.data),
    enabled: !!id,
    refetchInterval: 10_000,
    refetchIntervalInBackground: false,
  })

  const triggerMutation = useMutation({
    mutationFn: () => pipelinesApi.trigger(id!),
    onSuccess: (res) => {
      toast({ title: 'Pipeline triggered' })
      navigate(`/runs/${res.data.job_id}`)
    },
  })

  const scheduleMutation = useMutation({
    mutationFn: (expr: string) => pipelinesApi.setSchedule(id!, { cron_expr: expr, is_active: true }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.pipelines.detail(id!) })
      toast({ title: 'Schedule saved' })
      setShowScheduleForm(false)
    },
  })

  const deleteScheduleMutation = useMutation({
    mutationFn: () => pipelinesApi.deleteSchedule(id!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.pipelines.detail(id!) })
      toast({ title: 'Schedule removed' })
    },
  })

  if (isLoading) return <div className="flex justify-center py-12"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" /></div>
  if (error || !pipeline) return <ApiErrorMessage error={error} />

  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader
        title={pipeline.name}
        breadcrumb={<Link to="/pipelines" className="hover:underline">Pipelines</Link>}
        action={
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => navigate(`/pipelines/${id}/edit`)}>
              <Edit className="h-4 w-4 mr-2" />
              Edit
            </Button>
            <Button onClick={() => triggerMutation.mutate()} disabled={triggerMutation.isPending}>
              <Play className="h-4 w-4 mr-2" />
              Run Now
            </Button>
          </div>
        }
      />

      <Card>
        <CardHeader><CardTitle>Configuration</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm">
          <Row label="Extraction Mode">
            <Badge variant="secondary">{pipeline.extraction_mode}</Badge>
          </Row>
          {pipeline.incremental_key && <Row label="Incremental Key"><code className="font-mono">{pipeline.incremental_key}</code></Row>}
          {pipeline.last_watermark && <Row label="Last Watermark"><code className="font-mono text-xs">{pipeline.last_watermark}</code></Row>}
          <Row label="Destination Prefix"><code className="font-mono">{pipeline.dest_prefix}</code></Row>
          {pipeline.min_rows_expected && <Row label="Min Rows">{pipeline.min_rows_expected.toLocaleString()}</Row>}
          {pipeline.max_rows_drop_pct && <Row label="Max Drop %">{pipeline.max_rows_drop_pct}%</Row>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Schedule</CardTitle>
            <Button variant="outline" size="sm" onClick={() => setShowScheduleForm(!showScheduleForm)}>
              {pipeline.schedule ? 'Edit' : 'Add Schedule'}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {pipeline.schedule ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <code className="font-mono text-sm">{pipeline.schedule.cron_expr}</code>
                  <div className="text-xs text-muted-foreground mt-1">
                    {pipeline.schedule.next_run_at && <>Next: {format(new Date(pipeline.schedule.next_run_at), 'PPp')}</>}
                  </div>
                </div>
                <ConfirmDialog
                  trigger={<Button variant="outline" size="sm">Remove</Button>}
                  title="Remove schedule?"
                  description="This will stop automatic runs for this pipeline."
                  confirmLabel="Remove"
                  destructive
                  onConfirm={() => deleteScheduleMutation.mutate()}
                />
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No schedule configured.</p>
          )}
          {showScheduleForm && (
            <div className="mt-4 space-y-3 border-t pt-4">
              <CronInput value={cronExpr} onChange={setCronExpr} />
              <Button
                size="sm"
                onClick={() => scheduleMutation.mutate(cronExpr)}
                disabled={!cronExpr || scheduleMutation.isPending}
              >
                Save Schedule
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Recent Runs</CardTitle>
            <Link to="/runs" className="text-sm text-primary hover:underline">View all</Link>
          </div>
        </CardHeader>
        <CardContent>
          {(runs?.items ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground py-2">No runs yet.</p>
          ) : (
            <div className="divide-y">
              {(runs?.items ?? []).map((run) => (
                <div key={run.id} className="flex items-center justify-between py-2">
                  <div>
                    <Link to={`/runs/${run.id}`} className="text-sm font-mono hover:underline text-primary">
                      {run.id.slice(0, 8)}…
                    </Link>
                    <div className="text-xs text-muted-foreground">
                      {run.created_at ? format(new Date(run.created_at), 'MMM d, HH:mm') : '—'}
                      {run.rows_read != null && <> · {run.rows_read.toLocaleString()} rows</>}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {run.schema_changed && <SchemaDriftBadge />}
                    <StatusBadge status={run.status} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between items-center py-1 border-b last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span>{children}</span>
    </div>
  )
}
