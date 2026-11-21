import { useState, useMemo, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  Calendar,
  Clock,
  Database,
  Edit2,
  FolderGit2,
  Play,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  X,
  Layers,
  History,
} from 'lucide-react'
import {
  pipelinesApi,
  connectionsApi,
  runsApi,
  type Connection,
  type JobRun,
} from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { EngineLogo } from '@/components/connections/EngineLogo'
import { EmptyState } from '@/components/shared/EmptyState'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import { BackfillDialog } from '@/components/pipelines/BackfillDialog'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { toast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'

function formatCronHuman(expr?: string | null): string {
  if (!expr) return 'Manual trigger only'
  const trimmed = expr.trim()
  if (trimmed === '0 0 * * *' || trimmed === '@daily') return 'Daily at 00:00'
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

type FilterMode = 'all' | 'active' | 'scheduled' | 'incremental' | 'inactive'

export function PipelinesPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [filterMode, setFilterMode] = useState<FilterMode>('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [triggeringId, setTriggeringId] = useState<string | null>(null)
  const searchInputRef = useRef<HTMLInputElement>(null)

  // Keyboard shortcut ⌘K / Ctrl+K
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        searchInputRef.current?.focus()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  const { data: pipelinesData, isLoading: isLoadingPipelines } = useQuery({
    queryKey: queryKeys.pipelines.all,
    queryFn: () => pipelinesApi.list().then((r) => r.data),
  })

  const { data: connectionsData } = useQuery({
    queryKey: queryKeys.connections.all,
    queryFn: () => connectionsApi.list().then((r) => r.data),
  })

  const { data: runsData } = useQuery({
    queryKey: queryKeys.runs.list({ limit: 100 }),
    queryFn: () => runsApi.list({ limit: 100 }).then((r) => r.data),
    refetchInterval: 10_000,
  })

  const pipelines = pipelinesData ?? []
  const connections = connectionsData ?? []
  const runs = runsData ?? []

  const connectionMap = useMemo(
    () => new Map<string, Connection>(connections.map((c) => [c.id, c])),
    [connections]
  )

  // Group latest run per pipeline
  const latestRunByPipeline = useMemo(() => {
    const map = new Map<string, JobRun>()
    for (const run of runs) {
      if (run.pipeline_id && !map.has(run.pipeline_id)) {
        map.set(run.pipeline_id, run)
      }
    }
    return map
  }, [runs])

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: (id: string) => pipelinesApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.pipelines.all })
      toast({ title: 'Pipeline deleted' })
    },
    onError: () => toast({ title: 'Failed to delete pipeline', variant: 'destructive' }),
  })

  // Trigger mutation
  const triggerMutation = useMutation({
    mutationFn: (id: string) => {
      setTriggeringId(id)
      return pipelinesApi.trigger(id)
    },
    onSuccess: (res) => {
      setTriggeringId(null)
      toast({
        title: 'Pipeline triggered',
        description: `Run ${res.data.job_id.slice(0, 8)}… enqueued`,
      })
      navigate(`/runs/${res.data.job_id}`)
    },
    onError: () => {
      setTriggeringId(null)
      toast({ title: 'Failed to trigger pipeline', variant: 'destructive' })
    },
  })

  // Quick stats
  const stats = useMemo(() => {
    return {
      all: pipelines.length,
      active: pipelines.filter((p) => p.is_active).length,
      scheduled: pipelines.filter((p) => p.is_active && !!p.schedule?.cron_expr).length,
      incremental: pipelines.filter((p) => p.extraction_mode === 'incremental').length,
      inactive: pipelines.filter((p) => !p.is_active).length,
    }
  }, [pipelines])

  // Filter and search
  const filteredPipelines = useMemo(() => {
    return pipelines.filter((pipeline) => {
      if (filterMode === 'active' && !pipeline.is_active) return false
      if (filterMode === 'inactive' && pipeline.is_active) return false
      if (filterMode === 'scheduled' && (!pipeline.is_active || !pipeline.schedule?.cron_expr)) return false
      if (filterMode === 'incremental' && pipeline.extraction_mode !== 'incremental') return false

      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase().trim()
        const sourceConn = connectionMap.get(pipeline.source_connection_id)
        const destConn = pipeline.dest_connection_id ? connectionMap.get(pipeline.dest_connection_id) : undefined

        const matchesName = pipeline.name.toLowerCase().includes(q)
        const matchesSource = sourceConn?.name.toLowerCase().includes(q) ?? false
        const matchesDest = destConn?.name.toLowerCase().includes(q) ?? false
        const matchesPath = pipeline.destination_path?.toLowerCase().includes(q) ?? false
        const matchesMode = pipeline.extraction_mode.toLowerCase().includes(q)

        if (!matchesName && !matchesSource && !matchesDest && !matchesPath && !matchesMode) {
          return false
        }
      }

      return true
    })
  }, [pipelines, filterMode, searchQuery, connectionMap])

  return (
    <div className="space-y-6">
      <PageHeader
        title="Pipelines"
        description="Ingestion workflows from operational databases and files to Parquet Lakehouse"
        action={
          <Button
            onClick={() => navigate('/pipelines/new')}
            className="shadow-apple-sm font-medium"
          >
            <Plus className="h-4 w-4 mr-2" />
            New Pipeline
          </Button>
        }
      />

      {/* Control Bar: Search & Status Filter Pills */}
      <div className="flex flex-col sm:flex-row gap-3 items-stretch sm:items-center justify-between">
        {/* Search input with keyboard shortcut indicator */}
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground/70" />
          <Input
            ref={searchInputRef}
            type="text"
            placeholder="Search pipelines, connections, destination... (⌘K)"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9 pr-9 h-9.5 text-sm bg-background border-border/80 focus-visible:ring-1"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground p-0.5"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        {/* Filter Pills */}
        <div className="flex items-center gap-1.5 overflow-x-auto pb-1 sm:pb-0">
          <Button
            variant={filterMode === 'all' ? 'default' : 'secondary'}
            size="sm"
            onClick={() => setFilterMode('all')}
            className={cn(
              'h-8 text-xs font-medium rounded-full px-3',
              filterMode === 'all'
                ? 'bg-foreground text-background hover:bg-foreground/90'
                : 'bg-secondary/70 hover:bg-secondary text-muted-foreground hover:text-foreground'
            )}
          >
            All
            <span className="ml-1.5 font-mono text-[11px] opacity-70">{stats.all}</span>
          </Button>

          <Button
            variant={filterMode === 'active' ? 'default' : 'secondary'}
            size="sm"
            onClick={() => setFilterMode('active')}
            className={cn(
              'h-8 text-xs font-medium rounded-full px-3',
              filterMode === 'active'
                ? 'bg-foreground text-background hover:bg-foreground/90'
                : 'bg-secondary/70 hover:bg-secondary text-muted-foreground hover:text-foreground'
            )}
          >
            Active
            <span className="ml-1.5 font-mono text-[11px] opacity-70">{stats.active}</span>
          </Button>

          <Button
            variant={filterMode === 'scheduled' ? 'default' : 'secondary'}
            size="sm"
            onClick={() => setFilterMode('scheduled')}
            className={cn(
              'h-8 text-xs font-medium rounded-full px-3',
              filterMode === 'scheduled'
                ? 'bg-foreground text-background hover:bg-foreground/90'
                : 'bg-secondary/70 hover:bg-secondary text-muted-foreground hover:text-foreground'
            )}
          >
            Scheduled
            <span className="ml-1.5 font-mono text-[11px] opacity-70">{stats.scheduled}</span>
          </Button>

          <Button
            variant={filterMode === 'incremental' ? 'default' : 'secondary'}
            size="sm"
            onClick={() => setFilterMode('incremental')}
            className={cn(
              'h-8 text-xs font-medium rounded-full px-3',
              filterMode === 'incremental'
                ? 'bg-foreground text-background hover:bg-foreground/90'
                : 'bg-secondary/70 hover:bg-secondary text-muted-foreground hover:text-foreground'
            )}
          >
            Incremental
            <span className="ml-1.5 font-mono text-[11px] opacity-70">{stats.incremental}</span>
          </Button>

          {stats.inactive > 0 && (
            <Button
              variant={filterMode === 'inactive' ? 'default' : 'secondary'}
              size="sm"
              onClick={() => setFilterMode('inactive')}
              className={cn(
                'h-8 text-xs font-medium rounded-full px-3',
                filterMode === 'inactive'
                  ? 'bg-foreground text-background hover:bg-foreground/90'
                  : 'bg-secondary/70 hover:bg-secondary text-muted-foreground hover:text-foreground'
              )}
            >
              Paused
              <span className="ml-1.5 font-mono text-[11px] opacity-70">{stats.inactive}</span>
            </Button>
          )}
        </div>
      </div>

      {/* Main Content */}
      {isLoadingPipelines ? (
        <div className="flex justify-center py-20">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
        </div>
      ) : pipelines.length === 0 ? (
        <EmptyState
          icon={FolderGit2}
          title="No pipelines created"
          description="Create your first pipeline to ingest structured tables or files into Parquet."
          action={{ label: 'New Pipeline', onClick: () => navigate('/pipelines/new') }}
        />
      ) : filteredPipelines.length === 0 ? (
        <Card className="py-12 border-dashed border-border/80">
          <div className="text-center space-y-3">
            <Search className="h-8 w-8 text-muted-foreground mx-auto opacity-50" />
            <div className="text-sm font-medium text-foreground">No pipelines matched the current filter</div>
            <div className="text-xs text-muted-foreground">
              Try searching with another keyword or resetting the filters.
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setSearchQuery('')
                setFilterMode('all')
              }}
              className="text-xs"
            >
              Reset Filters
            </Button>
          </div>
        </Card>
      ) : (
        <Card className="overflow-hidden border border-border/80 shadow-apple-sm">
          {/* Table Header Row */}
          <CardHeader className="bg-secondary/40 border-b border-border/60 py-3 px-5 hidden md:block">
            <div className="grid grid-cols-12 gap-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <div className="col-span-4">Pipeline & Engine Flow</div>
              <div className="col-span-3">Destination & Mode</div>
              <div className="col-span-3">Cadence & Schedule</div>
              <div className="col-span-2 text-right">Status & Actions</div>
            </div>
          </CardHeader>

          <CardContent className="p-0">
            <div className="divide-y divide-border/60">
              {filteredPipelines.map((pipeline) => {
                const sourceConn = connectionMap.get(pipeline.source_connection_id)
                const destConn = pipeline.dest_connection_id
                  ? connectionMap.get(pipeline.dest_connection_id)
                  : undefined
                const latestRun = latestRunByPipeline.get(pipeline.id)
                const isTriggering = triggeringId === pipeline.id && triggerMutation.isPending

                return (
                  <div
                    key={pipeline.id}
                    onClick={() => navigate(`/pipelines/${pipeline.id}`)}
                    className="px-5 py-4 hover:bg-foreground/[0.025] cursor-pointer transition-colors group flex flex-col md:grid md:grid-cols-12 md:gap-4 md:items-center gap-3.5"
                  >
                    {/* Col 1: Pipeline & Engine Flow (4 cols) */}
                    <div className="md:col-span-4 min-w-0 flex items-center gap-3.5">
                      {sourceConn ? (
                        <div className="flex items-center gap-1.5 shrink-0">
                          <EngineLogo type={sourceConn.type} name={sourceConn.name} size="sm" />
                          <ArrowRight className="h-3 w-3 text-muted-foreground/40 shrink-0" />
                          {destConn ? (
                            <EngineLogo type={destConn.type} name={destConn.name} size="sm" />
                          ) : (
                            <EngineLogo type="s3_parquet" name="S3 Lakehouse" size="sm" />
                          )}
                        </div>
                      ) : (
                        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-secondary border border-border/60 shrink-0">
                          <Database className="h-3.5 w-3.5 text-muted-foreground" />
                        </div>
                      )}

                      <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <Link
                            to={`/pipelines/${pipeline.id}`}
                            onClick={(e) => e.stopPropagation()}
                            className="text-[14.5px] font-semibold text-foreground group-hover:underline underline-offset-4 truncate max-w-[240px]"
                          >
                            {pipeline.name}
                          </Link>

                          {!pipeline.is_active && (
                            <Badge variant="secondary" className="text-[10px] px-1.5 py-0 font-mono text-muted-foreground">
                              Paused
                            </Badge>
                          )}
                        </div>

                        <div className="text-xs text-muted-foreground font-mono mt-0.5 truncate flex items-center gap-1.5">
                          <span>{sourceConn ? sourceConn.name : 'Unknown source'}</span>
                          <span>→</span>
                          <span className="truncate">{pipeline.destination_path || 'bronze/dataset'}</span>
                        </div>
                      </div>
                    </div>

                    {/* Col 2: Destination & Mode (3 cols) */}
                    <div className="md:col-span-3 text-xs">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <Badge
                          variant="secondary"
                          className={cn(
                            'text-[10.5px] px-2 py-0 font-mono capitalize',
                            pipeline.extraction_mode === 'incremental'
                              ? 'bg-blue-500/10 text-blue-700 dark:text-blue-300 border-blue-500/20'
                              : 'bg-secondary text-foreground'
                          )}
                        >
                          {pipeline.extraction_mode === 'incremental' ? 'Incremental' : 'Full Refresh'}
                        </Badge>

                        {pipeline.partition_by === 'ingest_date' && (
                          <Badge variant="outline" className="text-[10px] px-1.5 py-0 font-mono text-muted-foreground">
                            <Layers className="h-2.5 w-2.5 mr-1" />
                            Date Part.
                          </Badge>
                        )}
                      </div>

                      <div className="text-xs text-muted-foreground font-mono mt-1 truncate">
                        {pipeline.extraction_mode === 'incremental' && pipeline.incremental_key ? (
                          <span>key: <span className="text-foreground/90 font-medium">{pipeline.incremental_key}</span></span>
                        ) : (
                          <span>table reload</span>
                        )}
                      </div>
                    </div>

                    {/* Col 3: Cadence & Schedule (3 cols) */}
                    <div className="md:col-span-3 text-xs">
                      {pipeline.schedule?.cron_expr ? (
                        <div>
                          <div className="flex items-center gap-1.5 text-foreground font-medium">
                            <Clock className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                            <span className="truncate">{formatCronHuman(pipeline.schedule.cron_expr)}</span>
                          </div>
                          <div className="text-xs text-muted-foreground font-mono mt-0.5 tabular-nums flex items-center gap-1">
                            <span>cron:</span>
                            <span className="bg-secondary/60 px-1 py-0.5 rounded text-[11px]">
                              {pipeline.schedule.cron_expr}
                            </span>
                          </div>
                        </div>
                      ) : (
                        <div className="text-muted-foreground flex items-center gap-1.5">
                          <Calendar className="h-3.5 w-3.5 text-muted-foreground/60 shrink-0" />
                          <span>On demand (manual)</span>
                        </div>
                      )}
                    </div>

                    {/* Col 4: Last Run Status & Actions (2 cols) */}
                    <div className="md:col-span-2 flex items-center justify-end gap-2 shrink-0">
                      {latestRun ? (
                        <StatusBadge status={latestRun.status} />
                      ) : (
                        <span className="text-[11px] font-mono text-muted-foreground/60">No runs</span>
                      )}

                      {/* Trigger Run Button */}
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation()
                          triggerMutation.mutate(pipeline.id)
                        }}
                        disabled={isTriggering}
                        className="h-8 px-2.5 text-xs font-medium hover:bg-primary hover:text-primary-foreground transition-colors ml-1"
                      >
                        {isTriggering ? (
                          <RefreshCw className="h-3 w-3 animate-spin mr-1" />
                        ) : (
                          <Play className="h-3 w-3 mr-1" />
                        )}
                        Run
                      </Button>

                      {/* Quick Backfill (Incremental only) */}
                      {pipeline.extraction_mode === 'incremental' && (
                        <div onClick={(e) => e.stopPropagation()}>
                          <BackfillDialog
                            pipeline={pipeline}
                            trigger={
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8 text-muted-foreground hover:text-foreground hidden sm:inline-flex"
                                title="Historical Backfill"
                              >
                                <History className="h-3.5 w-3.5" />
                              </Button>
                            }
                          />
                        </div>
                      )}

                      {/* Quick Edit */}
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={(e) => {
                          e.stopPropagation()
                          navigate(`/pipelines/${pipeline.id}/edit`)
                        }}
                        className="h-8 w-8 text-muted-foreground hover:text-foreground hidden sm:inline-flex"
                        title="Edit Pipeline"
                      >
                        <Edit2 className="h-3.5 w-3.5" />
                      </Button>

                      {/* Delete */}
                      <ConfirmDialog
                        trigger={
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={(e) => e.stopPropagation()}
                            className="h-8 w-8 text-muted-foreground hover:text-destructive"
                            title="Delete Pipeline"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        }
                        title="Delete pipeline?"
                        description={`Are you sure you want to delete "${pipeline.name}"? This action cannot be undone.`}
                        confirmLabel="Delete"
                        destructive
                        onConfirm={() => deleteMutation.mutate(pipeline.id)}
                      />

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
