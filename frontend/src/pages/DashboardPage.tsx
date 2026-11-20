import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  GitBranch,
  Layers,
  Loader2,
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
import { EngineLogo } from '@/components/connections/EngineLogo'
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
  const [statusFilter, setStatusFilter] = useState<'all' | 'running' | 'success' | 'failed'>('all')

  const { data: runs } = useQuery({
    queryKey: queryKeys.runs.list({ limit: 15 }),
    queryFn: () => runsApi.list({ limit: 15 }).then((r) => r.data),
    refetchInterval: 6_000,
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
  const runningRuns = recentRuns.filter((r) => r.status === 'running').length

  // Filtered runs for the feed
  const filteredRuns = recentRuns.filter((r) => {
    if (statusFilter === 'all') return true
    return r.status === statusFilter
  })

  return (
    <div className="space-y-6">
      {/* Top Header with Live Engine Status Badge */}
      <PageHeader
        title="Dashboard"
        description="Data ingestion streams, execution telemetry, and pipeline dispatch."
        breadcrumb={
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-secondary/60 border border-border/80 text-xs font-mono">
            {runningRuns > 0 ? (
              <>
                <span className="h-2 w-2 rounded-full bg-blue-500 animate-pulse" />
                <span className="text-blue-500 font-semibold">{runningRuns} job{runningRuns > 1 ? 's' : ''} running</span>
              </>
            ) : (
              <>
                <span className="h-2 w-2 rounded-full bg-emerald-500" />
                <span className="text-muted-foreground">{activePipelines} active pipeline{activePipelines === 1 ? '' : 's'} · Engine Ready</span>
              </>
            )}
          </div>
        }
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

      {/* Main Split Layout: 65% Ingestion Stream / 35% Pipelines & Quick Trigger */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* Left Column (65% / 8 cols): Live Ingestion Stream */}
        <div className="lg:col-span-8 space-y-4">
          <Card className="overflow-hidden">
            <CardHeader className="border-b border-border/60 py-3.5 px-5">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                <div className="flex items-center gap-2.5">
                  <CardTitle className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    <Activity className="h-3.5 w-3.5" /> Recent Ingestion Activity
                  </CardTitle>
                  <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                    Live
                  </div>
                </div>

                {/* Filter Pill Controls */}
                <div className="flex items-center gap-1 bg-secondary/50 p-0.5 rounded-lg border border-border/50 text-xs">
                  {(['all', 'success', 'failed', 'running'] as const).map((filter) => (
                    <button
                      key={filter}
                      type="button"
                      onClick={() => setStatusFilter(filter)}
                      className={cn(
                        'px-2.5 py-1 rounded-md text-[11px] font-medium capitalize transition-all duration-150',
                        statusFilter === filter
                          ? 'bg-card text-foreground shadow-apple-sm font-semibold'
                          : 'text-muted-foreground hover:text-foreground',
                      )}
                    >
                      {filter}
                    </button>
                  ))}
                </div>
              </div>
            </CardHeader>

            <CardContent className="p-0">
              {filteredRuns.length === 0 ? (
                <div className="py-14 px-4 text-center">
                  <Activity className="h-8 w-8 text-muted-foreground/30 mx-auto mb-2" />
                  <p className="text-sm font-medium text-foreground">No runs matching filter</p>
                  <p className="text-xs text-muted-foreground mt-1">
                    {statusFilter !== 'all' ? 'Try switching back to "all" to see all runs.' : 'Trigger a pipeline to see executions appear here.'}
                  </p>
                </div>
              ) : (
                <div className="divide-y divide-border/60">
                  {filteredRuns.map((run) => {
                    const pipeline = run.pipeline_id ? pipelineMap.get(run.pipeline_id) : undefined
                    const sourceConn = pipeline ? connectionMap.get(pipeline.source_connection_id) : undefined
                    const destConn = pipeline?.dest_connection_id ? connectionMap.get(pipeline.dest_connection_id) : undefined

                    return (
                      <div
                        key={run.id}
                        onClick={() => navigate(`/runs/${run.id}`)}
                        className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 px-5 py-3.5 hover:bg-foreground/[0.02] cursor-pointer transition-colors group"
                      >
                        <div className="min-w-0 flex items-center gap-3.5">
                          {/* Engine Flow Logos */}
                          {sourceConn && (
                            <div className="flex items-center gap-1.5 shrink-0">
                              <EngineLogo type={sourceConn.type} name={sourceConn.name} size="sm" />
                              {destConn && (
                                <>
                                  <ArrowRight className="h-2.5 w-2.5 text-muted-foreground/40 shrink-0" />
                                  <EngineLogo type={destConn.type} name={destConn.name} size="sm" />
                                </>
                              )}
                            </div>
                          )}

                          <div className="min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              {pipeline ? (
                                <span className="text-xs font-semibold text-foreground group-hover:underline underline-offset-4 truncate max-w-[240px]">
                                  {pipeline.name}
                                </span>
                              ) : (
                                <span className="text-xs font-semibold text-foreground">Ad-hoc Job</span>
                              )}

                              <span className="font-mono text-xs text-muted-foreground">
                                #{run.id.slice(0, 8)}
                              </span>

                              <Badge variant="secondary" className="text-[10px] px-1.5 py-0 font-normal">
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
                          <ArrowRight className="h-3.5 w-3.5 text-muted-foreground/40 group-hover:text-foreground group-hover:translate-x-0.5 transition-all" />
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </CardContent>

            <div className="p-3 border-t border-border/60 bg-secondary/20 flex justify-end">
              <Link
                to="/runs"
                className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground font-medium transition-colors"
              >
                <span>View full run history</span>
                <ArrowUpRight className="h-3 w-3" />
              </Link>
            </div>
          </Card>
        </div>

        {/* Right Column (35% / 4 cols): Pipelines & Direct Action Panel */}
        <div className="lg:col-span-4 space-y-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between border-b border-border/60 py-3.5 px-5">
              <CardTitle className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                <GitBranch className="h-3.5 w-3.5" /> Pipelines
              </CardTitle>
              <Link
                to="/pipelines"
                className="text-xs text-muted-foreground hover:text-foreground transition-colors inline-flex items-center gap-1"
              >
                <span>All ({pipelineList.length})</span>
                <ArrowUpRight className="h-3 w-3" />
              </Link>
            </CardHeader>

            <CardContent className="p-0 divide-y divide-border/60">
              {pipelineList.length === 0 ? (
                <div className="py-8 px-4 text-center">
                  <p className="text-xs text-muted-foreground">No pipelines created yet.</p>
                  <Button
                    size="sm"
                    variant="outline"
                    className="mt-3 text-xs"
                    onClick={() => navigate('/pipelines/new')}
                  >
                    <Plus className="h-3.5 w-3.5 mr-1" />
                    Create Pipeline
                  </Button>
                </div>
              ) : (
                pipelineList.slice(0, 6).map((pipeline) => {
                  const source = connectionMap.get(pipeline.source_connection_id)
                  const dest = pipeline.dest_connection_id ? connectionMap.get(pipeline.dest_connection_id) : undefined
                  const isPending = triggerMutation.isPending && triggerMutation.variables === pipeline.id

                  return (
                    <div
                      key={pipeline.id}
                      className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-foreground/[0.02] transition-colors"
                    >
                      <div className="min-w-0 flex items-center gap-2.5">
                        <div className="flex items-center gap-1 shrink-0">
                          {source && <EngineLogo type={source.type} name={source.name} size="sm" />}
                          {dest && (
                            <>
                              <ArrowRight className="h-2 w-2 text-muted-foreground/30 shrink-0" />
                              <EngineLogo type={dest.type} name={dest.name} size="sm" />
                            </>
                          )}
                        </div>
                        <div className="min-w-0">
                          <Link
                            to={`/pipelines/${pipeline.id}`}
                            className="text-xs font-semibold text-foreground hover:underline block truncate max-w-[150px]"
                          >
                            {pipeline.name}
                          </Link>
                          <div className="text-[11px] text-muted-foreground font-mono truncate mt-0.5">
                            {pipeline.schedule ? pipeline.schedule.cron_expr : 'manual'} · {pipeline.extraction_mode}
                          </div>
                        </div>
                      </div>

                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 px-2.5 text-xs shrink-0 gap-1 hover:bg-foreground hover:text-background transition-colors"
                        onClick={() => triggerMutation.mutate(pipeline.id)}
                        disabled={isPending}
                        title="Run this pipeline now"
                      >
                        {isPending ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <>
                            <Play className="h-2.5 w-2.5 fill-current" />
                            <span>Run</span>
                          </>
                        )}
                      </Button>
                    </div>
                  )
                })
              )}
            </CardContent>
          </Card>

          {/* Architecture Status Footer Card */}
          <div className="px-4 py-3 rounded-xl border border-border/60 bg-secondary/30 flex items-center justify-between text-xs text-muted-foreground">
            <span className="flex items-center gap-2">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
              <span>Bronze Lake Ingestion</span>
            </span>
            <span className="font-mono text-[11px] text-foreground/70">PyArrow · Parquet</span>
          </div>
        </div>
      </div>
    </div>
  )
}
