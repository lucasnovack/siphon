import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { Activity, ArrowRight, Database, Search, X } from 'lucide-react'
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
import { EmptyState } from '@/components/shared/EmptyState'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
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

type StatusFilter = 'all' | 'running' | 'success' | 'failed' | 'queued'

export function RunsPage() {
  const navigate = useNavigate()
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [searchQuery, setSearchQuery] = useState('')

  const { data: runsData, isLoading } = useQuery({
    queryKey: queryKeys.runs.list({ limit: 100 }),
    queryFn: () => runsApi.list({ limit: 100 }).then((r) => r.data),
    refetchInterval: 8_000,
    refetchIntervalInBackground: false,
  })

  const { data: pipelinesData } = useQuery({
    queryKey: queryKeys.pipelines.all,
    queryFn: () => pipelinesApi.list().then((r) => r.data),
  })

  const { data: connectionsData } = useQuery({
    queryKey: queryKeys.connections.all,
    queryFn: () => connectionsApi.list().then((r) => r.data),
  })

  const runs = runsData ?? []
  const pipelines = pipelinesData ?? []
  const connections = connectionsData ?? []

  const pipelineMap = useMemo(() => new Map<string, Pipeline>(pipelines.map((p) => [p.id, p])), [pipelines])
  const connectionMap = useMemo(() => new Map<string, Connection>(connections.map((c) => [c.id, c])), [connections])

  // Count by status
  const counts = useMemo(() => {
    return {
      all: runs.length,
      running: runs.filter((r) => r.status === 'running').length,
      success: runs.filter((r) => r.status === 'success').length,
      failed: runs.filter((r) => r.status === 'failed').length,
      queued: runs.filter((r) => r.status === 'queued').length,
    }
  }, [runs])

  // Filtered & searched runs
  const filteredRuns = useMemo(() => {
    return runs.filter((run) => {
      // Status filter
      if (statusFilter !== 'all' && run.status !== statusFilter) {
        return false
      }

      // Search filter
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase().trim()
        const pipeline = run.pipeline_id ? pipelineMap.get(run.pipeline_id) : undefined
        const pipelineName = pipeline?.name?.toLowerCase() ?? ''
        const runId = run.id.toLowerCase()
        const trigger = run.triggered_by.toLowerCase()

        if (!pipelineName.includes(q) && !runId.includes(q) && !trigger.includes(q)) {
          return false
        }
      }

      return true
    })
  }, [runs, statusFilter, searchQuery, pipelineMap])

  return (
    <div className="space-y-6">
      <PageHeader
        title="Runs"
        description="Global ingestion execution history, schema drift alerts, and execution logs."
        breadcrumb={
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-secondary/60 border border-border/80 text-xs font-mono">
            {counts.running > 0 ? (
              <>
                <span className="h-2 w-2 rounded-full bg-blue-500 animate-pulse" />
                <span className="text-blue-500 font-semibold">{counts.running} running</span>
              </>
            ) : (
              <>
                <span className="h-2 w-2 rounded-full bg-emerald-500" />
                <span className="text-muted-foreground">{counts.all} total executions</span>
              </>
            )}
          </div>
        }
      />

      {/* Filter and Search Bar */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
        {/* Status Filter Buttons */}
        <div className="flex items-center gap-1.5 overflow-x-auto pb-1 sm:pb-0">
          {(['all', 'success', 'failed', 'running', 'queued'] as const).map((filter) => {
            const count = counts[filter]
            const active = statusFilter === filter

            return (
              <button
                key={filter}
                type="button"
                onClick={() => setStatusFilter(filter)}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs sm:text-[13px] font-medium border transition-all duration-150 capitalize shrink-0 select-none',
                  active
                    ? 'border-foreground bg-foreground text-background font-semibold shadow-apple-sm'
                    : 'border-border/80 bg-card text-muted-foreground hover:text-foreground hover:border-border',
                )}
              >
                <span>{filter}</span>
                <span
                  className={cn(
                    'font-mono text-[11px] px-1.5 py-0.2 rounded-md',
                    active ? 'bg-background/20 text-background' : 'bg-secondary text-muted-foreground',
                  )}
                >
                  {count}
                </span>
              </button>
            )
          })}
        </div>

        {/* Search Input */}
        <div className="relative w-full sm:w-72 shrink-0">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search by pipeline or run ID…"
            className="pl-9 pr-8 text-xs sm:text-[13px] h-9 rounded-xl bg-card border-border/80"
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => setSearchQuery('')}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 p-0.5 text-muted-foreground hover:text-foreground"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>

      {/* Main Runs List Card */}
      {isLoading ? (
        <div className="flex justify-center py-16">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-foreground" />
        </div>
      ) : filteredRuns.length === 0 ? (
        <Card className="border border-border/80">
          <CardContent className="p-0">
            <EmptyState
              icon={Activity}
              title={runs.length === 0 ? 'No runs yet' : 'No matching runs found'}
              description={
                runs.length === 0
                  ? 'Runs will appear here when your pipelines are triggered manually or by schedule.'
                  : 'Try clearing your search query or switching status filters.'
              }
              action={
                searchQuery || statusFilter !== 'all'
                  ? {
                      label: 'Clear Filters',
                      onClick: () => {
                        setSearchQuery('')
                        setStatusFilter('all')
                      },
                    }
                  : undefined
              }
            />
          </CardContent>
        </Card>
      ) : (
        <Card className="overflow-hidden border border-border/80 shadow-apple-sm">
          {/* Table Header Row */}
          <CardHeader className="bg-secondary/40 border-b border-border/60 py-3 px-5 hidden md:block">
            <div className="grid grid-cols-12 gap-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <div className="col-span-5">Pipeline & Execution Flow</div>
              <div className="col-span-2">Trigger & Date</div>
              <div className="col-span-2">Ingestion Volume</div>
              <div className="col-span-1">Duration</div>
              <div className="col-span-2 text-right">Status</div>
            </div>
          </CardHeader>

          <CardContent className="p-0">
            <div className="divide-y divide-border/60">
              {filteredRuns.map((run) => {
                const pipeline = run.pipeline_id ? pipelineMap.get(run.pipeline_id) : undefined
                const sourceConn = pipeline ? connectionMap.get(pipeline.source_connection_id) : undefined
                const destConn = pipeline?.dest_connection_id ? connectionMap.get(pipeline.dest_connection_id) : undefined
                const rows = run.rows_written ?? run.rows_read

                return (
                  <div
                    key={run.id}
                    onClick={() => navigate(`/runs/${run.id}`)}
                    className="px-5 py-4 hover:bg-foreground/[0.025] cursor-pointer transition-colors group flex flex-col md:grid md:grid-cols-12 md:gap-4 md:items-center gap-3"
                  >
                    {/* Col 1: Pipeline & Conector Flow (5 cols) */}
                    <div className="md:col-span-5 min-w-0 flex items-center gap-3.5">
                      {sourceConn ? (
                        <div className="flex items-center gap-1.5 shrink-0">
                          <EngineLogo type={sourceConn.type} name={sourceConn.name} size="sm" />
                          {destConn && (
                            <>
                              <ArrowRight className="h-3 w-3 text-muted-foreground/40 shrink-0" />
                              <EngineLogo type={destConn.type} name={destConn.name} size="sm" />
                            </>
                          )}
                        </div>
                      ) : (
                        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-secondary border border-border/60 shrink-0">
                          <Database className="h-3.5 w-3.5 text-muted-foreground" />
                        </div>
                      )}

                      <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          {pipeline ? (
                            <Link
                              to={`/pipelines/${pipeline.id}`}
                              onClick={(e) => e.stopPropagation()}
                              className="text-sm font-semibold text-foreground group-hover:underline underline-offset-4 truncate max-w-[260px]"
                            >
                              {pipeline.name}
                            </Link>
                          ) : (
                            <span className="text-sm font-semibold text-foreground">Ad-hoc Job</span>
                          )}

                          <span className="font-mono text-xs text-muted-foreground">
                            #{run.id.slice(0, 8)}
                          </span>
                        </div>

                        <div className="text-xs text-muted-foreground font-mono mt-0.5 truncate">
                          {pipeline ? (
                            <span>{pipeline.extraction_mode} · {pipeline.destination_path || 'bronze/layer'}</span>
                          ) : (
                            <span>standalone execution</span>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Col 2: Trigger & Timestamp (2 cols) */}
                    <div className="md:col-span-2 text-xs">
                      <div className="flex items-center gap-1.5">
                        <Badge variant="secondary" className="text-[10.5px] px-2 py-0 capitalize font-mono">
                          {run.triggered_by}
                        </Badge>
                      </div>
                      <div className="text-xs text-muted-foreground font-mono mt-1 tabular-nums">
                        {run.created_at ? format(new Date(run.created_at), 'MMM d, HH:mm:ss') : '—'}
                      </div>
                    </div>

                    {/* Col 3: Rows Ingested (2 cols) */}
                    <div className="md:col-span-2 text-xs">
                      {rows != null ? (
                        <div>
                          <span className="font-mono text-sm font-semibold text-foreground tabular-nums">
                            {rows.toLocaleString()}
                          </span>
                          <span className="text-xs text-muted-foreground ml-1">rows</span>
                        </div>
                      ) : (
                        <span className="text-muted-foreground font-mono text-xs">—</span>
                      )}
                      <div className="text-[11px] text-muted-foreground">Parquet written</div>
                    </div>

                    {/* Col 4: Duration (1 col) */}
                    <div className="md:col-span-1 text-xs font-mono tabular-nums text-foreground/90">
                      {formatDuration(run.started_at, run.finished_at)}
                    </div>

                    {/* Col 5: Status Badges & Action Arrow (2 cols) */}
                    <div className="md:col-span-2 flex items-center justify-end gap-2 shrink-0">
                      {run.schema_changed && <SchemaDriftBadge />}
                      <StatusBadge status={run.status} />
                      <ArrowRight className="h-4 w-4 text-muted-foreground/30 group-hover:text-foreground group-hover:translate-x-0.5 transition-all hidden md:block" />
                    </div>
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
