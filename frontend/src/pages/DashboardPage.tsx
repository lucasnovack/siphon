import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  Database,
  GitBranch,
  Layers,
  Play,
  Plus,
} from 'lucide-react'
import { format } from 'date-fns'
import {
  runsApi,
  pipelinesApi,
  connectionsApi,
  type Pipeline,
  type Connection,
} from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { SchemaDriftBadge } from '@/components/shared/SchemaDriftBadge'
import { EngineLogo, getConnectorMeta } from '@/components/connections/EngineLogo'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { toast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'

function formatDuration(startedAt?: string | null, finishedAt?: string | null): string {
  if (!startedAt || !finishedAt) return '—'
  const start = new Date(startedAt).getTime()
  const end = new Date(finishedAt).getTime()
  const diffSec = Math.max(0, Math.round((end - start) / 1000))
  if (diffSec < 60) return `${diffSec}s`
  const mins = Math.floor(diffSec / 60)
  const secs = diffSec % 60
  return `${mins}m ${secs}s`
}

export function DashboardPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()

  const { data: runs } = useQuery({
    queryKey: queryKeys.runs.list({ limit: 10 }),
    queryFn: () => runsApi.list({ limit: 10 }).then((r) => r.data),
    refetchInterval: 8_000,
    refetchIntervalInBackground: false,
  })

  const { data: pipelines } = useQuery({
    queryKey: queryKeys.pipelines.all,
    queryFn: () => pipelinesApi.list().then((r) => r.data),
  })

  const { data: connections } = useQuery({
    queryKey: queryKeys.connections.all,
    queryFn: () => connectionsApi.list().then((r) => r.data),
  })

  const triggerMutation = useMutation({
    mutationFn: (id: string) => pipelinesApi.trigger(id),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: queryKeys.runs.all })
      toast({ title: 'Pipeline triggered', description: `Job #${res.data.job_id.slice(0, 8)} started` })
      navigate(`/runs/${res.data.job_id}`)
    },
    onError: () => toast({ title: 'Failed to trigger pipeline', variant: 'destructive' }),
  })

  const recentRuns = runs ?? []
  const pipelineList = pipelines ?? []
  const connectionList = connections ?? []

  const pipelineMap = new Map<string, Pipeline>(pipelineList.map((p) => [p.id, p]))
  const connectionMap = new Map<string, Connection>(connectionList.map((c) => [c.id, c]))

  const activePipelines = pipelineList.filter((p) => p.is_active).length
  const totalPipelines = pipelineList.length

  const sourceConnections = connectionList.filter((c) => getConnectorMeta(c.type).role === 'source')
  const destConnections = connectionList.filter((c) => getConnectorMeta(c.type).role === 'destination')

  const totalRecentRuns = recentRuns.length
  const successRuns = recentRuns.filter((r) => r.status === 'success').length
  const failedRuns = recentRuns.filter((r) => r.status === 'failed').length
  const runningRuns = recentRuns.filter((r) => r.status === 'running').length

  const successRate = totalRecentRuns > 0 ? Math.round((successRuns / totalRecentRuns) * 100) : null
  const totalRowsIngested = recentRuns.reduce((acc, r) => acc + (r.rows_written ?? r.rows_read ?? 0), 0)

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        description="Telemetry and pipeline execution overview."
        action={
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => navigate('/connections/new')}>
              <Layers className="h-3.5 w-3.5 mr-1.5" />
              New Connection
            </Button>
            <Button size="sm" onClick={() => navigate('/pipelines/new')}>
              <Plus className="h-3.5 w-3.5 mr-1.5" />
              New Pipeline
            </Button>
          </div>
        }
      />

      {/* Top KPI Metrics Bar */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Pipelines */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-medium uppercase tracking-wider text-muted-foreground flex items-center justify-between">
              <span>Pipelines</span>
              <GitBranch className="h-3.5 w-3.5 text-muted-foreground/70" />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-semibold tracking-tight font-mono tabular-nums text-foreground">
                {activePipelines}
              </span>
              <span className="text-xs text-muted-foreground font-mono">/ {totalPipelines} active</span>
            </div>
            <Link
              to="/pipelines"
              className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground mt-2 transition-colors"
            >
              Manage pipelines
              <ArrowRight className="h-3 w-3" />
            </Link>
          </CardContent>
        </Card>

        {/* Rows Ingested */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-medium uppercase tracking-wider text-muted-foreground flex items-center justify-between">
              <span>Rows Ingested (Recent)</span>
              <Database className="h-3.5 w-3.5 text-muted-foreground/70" />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-semibold tracking-tight font-mono tabular-nums text-foreground">
              {totalRowsIngested.toLocaleString()}
            </div>
            <p className="text-xs text-muted-foreground mt-2">
              Bronze Parquet records
            </p>
          </CardContent>
        </Card>

        {/* Ingestion Success Rate */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-medium uppercase tracking-wider text-muted-foreground flex items-center justify-between">
              <span>Success Rate</span>
              <Activity className="h-3.5 w-3.5 text-muted-foreground/70" />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  'text-2xl font-semibold tracking-tight font-mono tabular-nums',
                  successRate === null
                    ? 'text-muted-foreground'
                    : successRate >= 90
                      ? 'text-emerald-500'
                      : successRate >= 70
                        ? 'text-amber-500'
                        : 'text-rose-500',
                )}
              >
                {successRate !== null ? `${successRate}%` : '—'}
              </span>
              {runningRuns > 0 && (
                <Badge variant="outline" className="text-[10px] bg-blue-500/10 text-blue-500 border-blue-500/20 font-mono">
                  {runningRuns} running
                </Badge>
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-2 tabular-nums font-mono">
              {successRuns} succeeded · {failedRuns} failed
            </p>
          </CardContent>
        </Card>

        {/* Storage Topology */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-medium uppercase tracking-wider text-muted-foreground flex items-center justify-between">
              <span>Connected Storage</span>
              <Layers className="h-3.5 w-3.5 text-muted-foreground/70" />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-semibold tracking-tight font-mono tabular-nums text-foreground">
                {sourceConnections.length}
              </span>
              <span className="text-xs text-muted-foreground">Sources ·</span>
              <span className="text-2xl font-semibold tracking-tight font-mono tabular-nums text-foreground">
                {destConnections.length}
              </span>
              <span className="text-xs text-muted-foreground">Destinations</span>
            </div>
            <Link
              to="/connections"
              className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground mt-2 transition-colors"
            >
              View connections
              <ArrowRight className="h-3 w-3" />
            </Link>
          </CardContent>
        </Card>
      </div>

      {/* Live Ingestion Feed (Recent Runs) */}
      <Card className="overflow-hidden">
        <CardHeader className="flex flex-row items-center justify-between border-b border-border/60 py-3.5 px-5">
          <div className="flex items-center gap-2.5">
            <CardTitle className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <Activity className="h-3.5 w-3.5" /> Recent Ingestion Runs
            </CardTitle>
            <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
              Live
            </div>
          </div>
          <Link
            to="/runs"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            View all history
            <ArrowUpRight className="h-3.5 w-3.5" />
          </Link>
        </CardHeader>
        <CardContent className="p-0">
          {recentRuns.length === 0 ? (
            <div className="py-12 px-4 text-center">
              <Activity className="h-8 w-8 text-muted-foreground/40 mx-auto mb-2" />
              <p className="text-sm font-medium text-foreground">No ingestion runs yet</p>
              <p className="text-xs text-muted-foreground mt-1">
                Trigger a pipeline to start extracting data into your Bronze storage.
              </p>
            </div>
          ) : (
            <div className="divide-y divide-border/60">
              {recentRuns.map((run) => {
                const pipeline = run.pipeline_id ? pipelineMap.get(run.pipeline_id) : undefined
                const sourceConn = pipeline ? connectionMap.get(pipeline.source_connection_id) : undefined
                const destConn = pipeline?.dest_connection_id ? connectionMap.get(pipeline.dest_connection_id) : undefined

                return (
                  <div
                    key={run.id}
                    className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 px-5 py-3.5 hover:bg-foreground/[0.02] transition-colors"
                  >
                    <div className="min-w-0 flex items-center gap-3.5">
                      {/* Engine Pair Flow Icons */}
                      {sourceConn && (
                        <div className="flex items-center gap-1.5 shrink-0">
                          <EngineLogo type={sourceConn.type} name={sourceConn.name} size="sm" />
                          {destConn && (
                            <>
                              <ArrowRight className="h-3 w-3 text-muted-foreground/40 shrink-0" />
                              <EngineLogo type={destConn.type} name={destConn.name} size="sm" />
                            </>
                          )}
                        </div>
                      )}

                      <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          {pipeline ? (
                            <Link
                              to={`/pipelines/${pipeline.id}`}
                              className="text-xs font-semibold text-foreground hover:underline underline-offset-4 truncate max-w-[280px]"
                            >
                              {pipeline.name}
                            </Link>
                          ) : (
                            <span className="text-xs font-semibold text-foreground">Ad-hoc Run</span>
                          )}

                          <Link
                            to={`/runs/${run.id}`}
                            className="font-mono text-xs text-muted-foreground hover:text-foreground transition-colors"
                          >
                            #{run.id.slice(0, 8)}
                          </Link>

                          <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                            {run.triggered_by}
                          </Badge>
                        </div>

                        <div className="flex items-center gap-2 text-xs text-muted-foreground mt-1 tabular-nums font-mono flex-wrap">
                          <span>{run.created_at ? format(new Date(run.created_at), 'MMM d, HH:mm:ss') : '—'}</span>
                          <span>·</span>
                          <span>{formatDuration(run.started_at, run.finished_at)}</span>
                          {(run.rows_written != null || run.rows_read != null) && (
                            <>
                              <span>·</span>
                              <span className="text-foreground/80 font-medium">
                                {(run.rows_written ?? run.rows_read ?? 0).toLocaleString()} rows
                              </span>
                            </>
                          )}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-2.5 shrink-0 self-end sm:self-center">
                      {run.schema_changed && <SchemaDriftBadge />}
                      <StatusBadge status={run.status} />
                      <Link
                        to={`/runs/${run.id}`}
                        className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary/60 transition-colors"
                        title="View Run Details"
                      >
                        <ArrowRight className="h-3.5 w-3.5" />
                      </Link>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Quick Pipelines Bar */}
      {pipelineList.length > 0 && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between border-b border-border/60 py-3 px-5">
            <CardTitle className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <GitBranch className="h-3.5 w-3.5" /> Quick Trigger Pipelines
            </CardTitle>
            <Link
              to="/pipelines"
              className="text-xs text-muted-foreground hover:text-foreground transition-colors inline-flex items-center gap-1"
            >
              All pipelines ({pipelineList.length})
              <ArrowUpRight className="h-3.5 w-3.5" />
            </Link>
          </CardHeader>
          <CardContent className="p-0 divide-y divide-border/60">
            {pipelineList.slice(0, 4).map((pipeline) => {
              const source = connectionMap.get(pipeline.source_connection_id)
              const dest = pipeline.dest_connection_id ? connectionMap.get(pipeline.dest_connection_id) : undefined

              return (
                <div
                  key={pipeline.id}
                  className="flex items-center justify-between gap-3 px-5 py-3 hover:bg-foreground/[0.02] transition-colors"
                >
                  <div className="min-w-0 flex items-center gap-3">
                    <div className="flex items-center gap-1.5 shrink-0">
                      {source && <EngineLogo type={source.type} name={source.name} size="sm" />}
                      {dest && (
                        <>
                          <ArrowRight className="h-2.5 w-2.5 text-muted-foreground/40 shrink-0" />
                          <EngineLogo type={dest.type} name={dest.name} size="sm" />
                        </>
                      )}
                    </div>
                    <div className="min-w-0">
                      <Link
                        to={`/pipelines/${pipeline.id}`}
                        className="text-xs font-semibold text-foreground hover:underline block truncate max-w-[280px]"
                      >
                        {pipeline.name}
                      </Link>
                      <div className="text-[11px] text-muted-foreground font-mono truncate mt-0.5">
                        {pipeline.extraction_mode} · {pipeline.schedule ? pipeline.schedule.cron_expr : 'Manual trigger'}
                      </div>
                    </div>
                  </div>

                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-xs shrink-0 gap-1.5"
                    onClick={() => triggerMutation.mutate(pipeline.id)}
                    disabled={triggerMutation.isPending}
                  >
                    <Play className="h-3 w-3" />
                    <span>Run</span>
                  </Button>
                </div>
              )
            })}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
