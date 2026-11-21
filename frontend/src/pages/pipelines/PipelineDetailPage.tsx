import { useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ArrowRight,
  Calendar,
  Clock,
  Code2,
  Database,
  Edit,
  History,
  Layers,
  Play,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { format } from 'date-fns'
import { pipelinesApi, connectionsApi, type Connection } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { SchemaDriftBadge } from '@/components/shared/SchemaDriftBadge'
import { EngineLogo } from '@/components/connections/EngineLogo'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import { ApiErrorMessage } from '@/components/shared/ApiErrorMessage'
import { CronInput } from '@/components/shared/CronInput'
import { BackfillDialog } from '@/components/pipelines/BackfillDialog'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { toast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'

function formatCronHuman(expr?: string | null): string {
  if (!expr) return 'Manual trigger only'
  const trimmed = expr.trim()
  if (trimmed === '0 0 * * *' || trimmed === '@daily') return 'Daily at midnight'
  if (trimmed === '0 2 * * *') return 'Daily at 02:00'
  if (trimmed === '0 3 * * *') return 'Daily at 03:00'
  if (trimmed === '0 4 * * *') return 'Daily at 04:00'
  if (trimmed === '0 * * * *' || trimmed === '@hourly') return 'Hourly'
  if (trimmed === '*/15 * * * *') return 'Every 15 min'
  if (trimmed === '*/30 * * * *') return 'Every 30 min'
  if (trimmed === '0 0 * * 1' || trimmed === '@weekly') return 'Weekly on Monday'

  const dailyMatch = trimmed.match(/^(\d+)\s+(\d+)\s+\*\s+\*\s+\*$/)
  if (dailyMatch) {
    const min = dailyMatch[1].padStart(2, '0')
    const hour = dailyMatch[2].padStart(2, '0')
    return `Daily at ${hour}:${min}`
  }

  const hourlyMatch = trimmed.match(/^(\d+)\s+\*\s+\*\s+\*\s+\*$/)
  if (hourlyMatch) {
    const min = hourlyMatch[1].padStart(2, '0')
    return `Hourly at :${min}`
  }

  return trimmed
}

export function PipelineDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [cronExpr, setCronExpr] = useState('')
  const [showScheduleForm, setShowScheduleForm] = useState(false)
  const [backfillOpen, setBackfillOpen] = useState(false)

  // Fetch Pipeline Detail
  const { data: pipeline, isLoading, error } = useQuery({
    queryKey: queryKeys.pipelines.detail(id!),
    queryFn: () => pipelinesApi.get(id!).then((r) => r.data),
    enabled: !!id,
  })

  // Fetch Connections for logos
  const { data: connectionsData } = useQuery({
    queryKey: queryKeys.connections.all,
    queryFn: () => connectionsApi.list().then((r) => r.data),
  })

  // Fetch Recent Runs for this pipeline
  const { data: runs } = useQuery({
    queryKey: queryKeys.pipelines.runs(id!, 1, 15),
    queryFn: () => pipelinesApi.runs(id!, 15).then((r) => r.data),
    enabled: !!id,
    refetchInterval: 8_000,
    refetchIntervalInBackground: false,
  })

  const connections = connectionsData ?? []
  const connectionMap = new Map<string, Connection>(connections.map((c) => [c.id, c]))
  const sourceConn = pipeline ? connectionMap.get(pipeline.source_connection_id) : undefined
  const destConn = pipeline?.dest_connection_id ? connectionMap.get(pipeline.dest_connection_id) : undefined

  // Run Now Mutation
  const triggerMutation = useMutation({
    mutationFn: () => pipelinesApi.trigger(id!),
    onSuccess: (res) => {
      toast({
        title: 'Pipeline triggered',
        description: `Run #${res.data.job_id.slice(0, 8)}… started`,
      })
      navigate(`/runs/${res.data.job_id}`)
    },
    onError: () => toast({ title: 'Failed to trigger pipeline', variant: 'destructive' }),
  })

  // Save Schedule Mutation
  const scheduleMutation = useMutation({
    mutationFn: (expr: string) =>
      pipelinesApi.setSchedule(id!, { cron_expr: expr, is_active: true }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.pipelines.detail(id!) })
      toast({ title: 'Schedule saved successfully' })
      setShowScheduleForm(false)
    },
    onError: () => toast({ title: 'Failed to save schedule', variant: 'destructive' }),
  })

  // Delete Schedule Mutation
  const deleteScheduleMutation = useMutation({
    mutationFn: () => pipelinesApi.deleteSchedule(id!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.pipelines.detail(id!) })
      toast({ title: 'Schedule removed' })
    },
    onError: () => toast({ title: 'Failed to remove schedule', variant: 'destructive' }),
  })

  if (isLoading) {
    return (
      <div className="flex justify-center py-24">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    )
  }

  if (error || !pipeline) return <ApiErrorMessage error={error} />

  const isIncremental = pipeline.extraction_mode === 'incremental'

  return (
    <div className="space-y-6 max-w-full">
      {/* Top Header */}
      <PageHeader
        title={pipeline.name}
        breadcrumb={
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Link to="/pipelines" className="hover:text-foreground hover:underline">
              Pipelines
            </Link>
            <span>/</span>
            <span className="text-foreground font-medium">{pipeline.name}</span>
          </div>
        }
        description={
          <div className="flex items-center gap-2 flex-wrap mt-0.5">
            <Badge
              variant="secondary"
              className={cn(
                'text-[10px] px-2 py-0 font-mono capitalize',
                isIncremental
                  ? 'bg-blue-500/10 text-blue-700 dark:text-blue-300 border border-blue-500/20'
                  : 'bg-secondary text-foreground'
              )}
            >
              {isIncremental ? 'Incremental' : 'Full Refresh'}
            </Badge>

            {!pipeline.is_active && (
              <Badge variant="secondary" className="text-[10px] px-2 py-0 text-muted-foreground">
                Paused
              </Badge>
            )}

            <span className="text-xs font-mono text-muted-foreground">
              {pipeline.destination_path || 'bronze/dataset'}
            </span>
          </div>
        }
        action={
          <div className="flex items-center gap-2">
            {/* Backfill Dialog Trigger */}
            <BackfillDialog
              pipeline={pipeline}
              open={backfillOpen}
              onOpenChange={setBackfillOpen}
              trigger={
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!isIncremental}
                  className="shadow-apple-sm font-medium h-9 text-xs"
                  title={
                    isIncremental
                      ? 'Rerun historical time window'
                      : 'Backfill is only available for incremental pipelines'
                  }
                >
                  <History className="h-3.5 w-3.5 mr-1.5" />
                  Backfill
                </Button>
              }
            />

            {/* Edit Button */}
            <Button
              variant="outline"
              size="sm"
              onClick={() => navigate(`/pipelines/${id}/edit`)}
              className="h-9 text-xs shadow-apple-sm"
            >
              <Edit className="h-3.5 w-3.5 mr-1.5" />
              Edit
            </Button>

            {/* Run Now Button */}
            <Button
              size="sm"
              onClick={() => triggerMutation.mutate()}
              disabled={triggerMutation.isPending}
              className="h-9 text-xs shadow-apple-sm font-medium"
            >
              {triggerMutation.isPending ? (
                <>
                  <RefreshCw className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                  Enqueuing…
                </>
              ) : (
                <>
                  <Play className="h-3.5 w-3.5 mr-1.5" />
                  Run Now
                </>
              )}
            </Button>
          </div>
        }
      />

      {/* Telemetry Strip / Metric Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {/* Card 1: Data Flow */}
        <Card className="border-border/80 shadow-apple-sm">
          <CardContent className="p-4 space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Engine Flow
            </div>
            <div className="flex items-center gap-2 pt-1">
              {sourceConn ? (
                <div className="flex items-center gap-1.5">
                  <EngineLogo type={sourceConn.type} name={sourceConn.name} size="sm" />
                  <ArrowRight className="h-3 w-3 text-muted-foreground/40 shrink-0" />
                  {destConn ? (
                    <EngineLogo type={destConn.type} name={destConn.name} size="sm" />
                  ) : (
                    <EngineLogo type="s3_parquet" name="S3 Lakehouse" size="sm" />
                  )}
                </div>
              ) : (
                <div className="flex h-7 w-7 items-center justify-center rounded bg-secondary">
                  <Database className="h-3.5 w-3.5 text-muted-foreground" />
                </div>
              )}
            </div>
            <div className="text-xs text-muted-foreground font-mono truncate pt-1">
              {sourceConn?.name ?? 'SQL Source'} → {destConn?.name ?? 'S3 / MinIO Lakehouse'}
            </div>
          </CardContent>
        </Card>

        {/* Card 2: Cadence & Schedule */}
        <Card className="border-border/80 shadow-apple-sm">
          <CardContent className="p-4 space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Ingestion Cadence
            </div>
            <div className="text-sm font-semibold text-foreground flex items-center gap-1.5 pt-1 truncate">
              <Clock className="h-4 w-4 text-muted-foreground shrink-0" />
              <span>{formatCronHuman(pipeline.schedule?.cron_expr)}</span>
            </div>
            <div className="text-xs text-muted-foreground font-mono truncate">
              {pipeline.schedule?.cron_expr ? (
                <span>cron: <code className="text-foreground">{pipeline.schedule.cron_expr}</code></span>
              ) : (
                <span>On-demand manual trigger</span>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Card 3: Watermark & Partitioning */}
        <Card className="border-border/80 shadow-apple-sm">
          <CardContent className="p-4 space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Watermark & State
            </div>
            <div className="text-sm font-semibold text-foreground flex items-center gap-1.5 pt-1 truncate">
              {isIncremental ? (
                <>
                  <Sparkles className="h-3.5 w-3.5 text-blue-500 shrink-0" />
                  <span className="font-mono">{pipeline.incremental_key}</span>
                </>
              ) : (
                <span className="text-muted-foreground">Full snapshot</span>
              )}
            </div>
            <div className="text-xs text-muted-foreground font-mono truncate">
              {pipeline.last_watermark ? (
                <span>last: <span className="text-foreground/90">{pipeline.last_watermark}</span></span>
              ) : (
                <span>initial / not recorded</span>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Card 4: Quality & SLA */}
        <Card className="border-border/80 shadow-apple-sm">
          <CardContent className="p-4 space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Guards & Quality
            </div>
            <div className="text-sm font-semibold text-foreground flex items-center gap-1.5 pt-1">
              <ShieldCheck className="h-4 w-4 text-emerald-500 shrink-0" />
              <span>
                {pipeline.min_rows_expected != null || pipeline.max_rows_drop_pct != null
                  ? 'Active DQ Guards'
                  : 'Standard Ingestion'}
              </span>
            </div>
            <div className="text-xs text-muted-foreground font-mono truncate">
              {pipeline.sla_minutes ? `${pipeline.sla_minutes}m SLA` : 'No SLA'} ·{' '}
              {pipeline.partition_by === 'ingest_date' ? 'Hive Date Part.' : 'Single Parquet'}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Main Grid: Details / Configuration & Schedule (Left) + Recent Runs (Right) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Column (7 cols): Configuration & Query */}
        <div className="lg:col-span-7 space-y-6">
          {/* Query & Extraction Card */}
          <Card className="border-border/80 shadow-apple-sm">
            <CardHeader className="py-3.5 px-5 bg-secondary/30 border-b border-border/60">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Code2 className="h-4 w-4 text-muted-foreground" />
                  <CardTitle className="text-sm font-semibold">Extraction Query</CardTitle>
                </div>
                <Badge variant="outline" className="font-mono text-[10px]">
                  SQL Source
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="p-4">
              <div className="rounded-lg bg-zinc-950 p-4 font-mono text-xs text-zinc-200 overflow-x-auto border border-zinc-800 leading-relaxed max-h-64">
                <pre>{pipeline.query}</pre>
              </div>
            </CardContent>
          </Card>

          {/* Schedule Manager Card */}
          <Card className="border-border/80 shadow-apple-sm">
            <CardHeader className="py-3.5 px-5 bg-secondary/30 border-b border-border/60">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Calendar className="h-4 w-4 text-muted-foreground" />
                  <CardTitle className="text-sm font-semibold">Automated Schedule</CardTitle>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowScheduleForm(!showScheduleForm)}
                  className="h-7 text-xs px-2.5"
                >
                  {pipeline.schedule ? (showScheduleForm ? 'Close' : 'Modify') : 'Configure'}
                </Button>
              </div>
            </CardHeader>

            <CardContent className="p-5 space-y-4">
              {pipeline.schedule ? (
                <div className="flex items-center justify-between">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <code className="font-mono text-xs bg-secondary px-2 py-0.5 rounded font-semibold text-foreground">
                        {pipeline.schedule.cron_expr}
                      </code>
                      <span className="text-xs text-muted-foreground">
                        ({formatCronHuman(pipeline.schedule.cron_expr)})
                      </span>
                    </div>

                    <div className="text-xs text-muted-foreground font-mono">
                      {pipeline.schedule.next_run_at ? (
                        <span>Next planned run: {format(new Date(pipeline.schedule.next_run_at), 'PPp')}</span>
                      ) : (
                        <span>Next run: pending scheduler calculation</span>
                      )}
                    </div>
                  </div>

                  <ConfirmDialog
                    trigger={
                      <Button variant="ghost" size="sm" className="h-7 text-xs text-destructive hover:text-destructive">
                        Disable
                      </Button>
                    }
                    title="Remove automated schedule?"
                    description="This will prevent Siphon from executing automatic runs for this pipeline."
                    confirmLabel="Disable Schedule"
                    destructive
                    onConfirm={() => deleteScheduleMutation.mutate()}
                  />
                </div>
              ) : (
                <div className="text-xs text-muted-foreground py-1">
                  No automated schedule configured. Pipeline only executes upon manual trigger or API call.
                </div>
              )}

              {showScheduleForm && (
                <div className="pt-3 border-t border-border/60 space-y-3">
                  <div className="text-xs font-semibold text-foreground">Set Cron Cadence</div>
                  <CronInput value={cronExpr} onChange={setCronExpr} />
                  <div className="flex justify-end gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setShowScheduleForm(false)}
                      className="text-xs h-8"
                    >
                      Cancel
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => scheduleMutation.mutate(cronExpr)}
                      disabled={!cronExpr || scheduleMutation.isPending}
                      className="text-xs h-8 shadow-apple-sm font-medium"
                    >
                      Save Cadence
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Right Column (5 cols): Recent Ingestions */}
        <div className="lg:col-span-5 space-y-6">
          <Card className="border-border/80 shadow-apple-sm overflow-hidden">
            <CardHeader className="py-3.5 px-5 bg-secondary/30 border-b border-border/60">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Layers className="h-4 w-4 text-muted-foreground" />
                  <CardTitle className="text-sm font-semibold">Execution History</CardTitle>
                </div>
                <Link to="/runs" className="text-xs text-primary hover:underline font-medium">
                  View all runs →
                </Link>
              </div>
            </CardHeader>

            <CardContent className="p-0">
              {(runs ?? []).length === 0 ? (
                <div className="py-12 text-center text-xs text-muted-foreground">
                  No execution runs recorded yet.
                </div>
              ) : (
                <div className="divide-y divide-border/60">
                  {(runs ?? []).map((run) => (
                    <div
                      key={run.id}
                      onClick={() => navigate(`/runs/${run.id}`)}
                      className="p-3.5 hover:bg-foreground/[0.025] cursor-pointer transition-colors flex items-center justify-between group"
                    >
                      <div className="min-w-0 pr-2">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs font-semibold text-foreground group-hover:underline">
                            #{run.id.slice(0, 8)}
                          </span>
                          <Badge
                            variant="secondary"
                            className="text-[10px] px-1.5 py-0 capitalize font-mono text-muted-foreground"
                          >
                            {run.triggered_by}
                          </Badge>
                        </div>

                        <div className="text-xs text-muted-foreground font-mono mt-0.5 tabular-nums">
                          {run.created_at ? format(new Date(run.created_at), 'MMM d, HH:mm') : '—'}
                          {run.rows_written != null && (
                            <> · <span className="text-foreground/90 font-medium">{run.rows_written.toLocaleString()}</span> rows</>
                          )}
                        </div>
                      </div>

                      <div className="flex items-center gap-2 shrink-0">
                        {run.schema_changed && <SchemaDriftBadge />}
                        <StatusBadge status={run.status} />
                        <ArrowRight className="h-3.5 w-3.5 text-muted-foreground/30 group-hover:text-foreground group-hover:translate-x-0.5 transition-all" />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
