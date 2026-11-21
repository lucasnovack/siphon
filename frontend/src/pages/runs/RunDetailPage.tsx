import { useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import { format } from 'date-fns'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock,
  Copy,
  Database,
  Layers,
  Play,
  RotateCcw,
} from 'lucide-react'
import { runsApi, pipelinesApi, connectionsApi, type Connection } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { SchemaDriftBadge } from '@/components/shared/SchemaDriftBadge'
import { EngineLogo } from '@/components/connections/EngineLogo'
import { ApiErrorMessage } from '@/components/shared/ApiErrorMessage'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import { LogViewer } from '@/components/runs/LogViewer'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { toast } from '@/hooks/use-toast'

function formatDuration(startedAt?: string | null, finishedAt?: string | null): string {
  if (!startedAt) return '—'
  const start = new Date(startedAt).getTime()
  const end = finishedAt ? new Date(finishedAt).getTime() : Date.now()
  const diffSec = Math.max(0, Math.round((end - start) / 1000))
  if (diffSec < 60) return `${diffSec}s`
  const mins = Math.floor(diffSec / 60)
  const secs = diffSec % 60
  return `${mins}m ${secs}s`
}

export function RunDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [copied, setCopied] = useState(false)

  // Fetch Run details with auto-refetch while running/queued
  const { data: run, isLoading, error } = useQuery({
    queryKey: queryKeys.runs.detail(id!),
    queryFn: () => runsApi.get(id!).then((r) => r.data),
    enabled: !!id,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'running' || status === 'queued' ? 2000 : false
    },
    refetchIntervalInBackground: false,
  })

  // Fetch Associated Pipeline
  const { data: pipeline } = useQuery({
    queryKey: queryKeys.pipelines.detail(run?.pipeline_id ?? ''),
    queryFn: () => pipelinesApi.get(run!.pipeline_id!).then((r) => r.data),
    enabled: !!run?.pipeline_id,
  })

  // Fetch Connections for logos and names
  const { data: connectionsData } = useQuery({
    queryKey: queryKeys.connections.all,
    queryFn: () => connectionsApi.list().then((r) => r.data),
  })

  const connections = connectionsData ?? []
  const connectionMap = new Map<string, Connection>(connections.map((c) => [c.id, c]))

  // Resolve source and dest connections
  const sourceConnId = run?.source_connection_id ?? pipeline?.source_connection_id
  const destConnId = pipeline?.dest_connection_id
  const sourceConn = sourceConnId ? connectionMap.get(sourceConnId) : undefined
  const destConn = destConnId ? connectionMap.get(destConnId) : undefined

  // Cancel mutation
  const cancelMutation = useMutation({
    mutationFn: () => runsApi.cancel(id!),
    onSuccess: () => toast({ title: 'Run cancellation requested' }),
    onError: () => toast({ title: 'Failed to cancel run', variant: 'destructive' }),
  })

  // Trigger rerun mutation
  const triggerMutation = useMutation({
    mutationFn: (pipelineId: string) => pipelinesApi.trigger(pipelineId),
    onSuccess: (res) => {
      toast({ title: 'Pipeline triggered', description: `New run enqueued` })
      navigate(`/runs/${res.data.job_id}`)
    },
    onError: () => toast({ title: 'Failed to trigger pipeline', variant: 'destructive' }),
  })

  if (isLoading) {
    return (
      <div className="flex justify-center py-24">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    )
  }

  if (error || !run) return <ApiErrorMessage error={error} />

  const isActive = run.status === 'running' || run.status === 'queued'
  const isFailed = run.status === 'failed'
  const destinationPath = run.destination_path || pipeline?.destination_path || 'bronze/dataset'
  const rows = run.rows_written ?? run.rows_read

  function handleCopyId() {
    navigator.clipboard.writeText(run!.id).then(() => {
      setCopied(true)
      toast({ title: 'Run ID copied to clipboard' })
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div className="space-y-6 max-w-full">
      {/* Header */}
      <PageHeader
        title={pipeline ? pipeline.name : `Run #${run.id.slice(0, 8)}`}
        description={
          <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground mt-0.5">
            <span>ID: {run.id}</span>
            <button
              onClick={handleCopyId}
              className="text-muted-foreground hover:text-foreground inline-flex items-center transition-colors"
              title="Copy ID"
            >
              <Copy className="h-3 w-3" />
            </button>
            {copied && <span className="text-emerald-500 font-sans text-xs">Copied!</span>}
          </div>
        }
        breadcrumb={
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Link to="/runs" className="hover:text-foreground hover:underline">
              Runs
            </Link>
            <span>/</span>
            <span className="text-foreground font-mono font-medium">#{run.id.slice(0, 8)}</span>
          </div>
        }
        action={
          <div className="flex items-center gap-2">
            {pipeline && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => triggerMutation.mutate(pipeline.id)}
                disabled={triggerMutation.isPending}
                className="h-9 px-3 text-xs font-medium shadow-apple-sm"
              >
                <Play className="h-3.5 w-3.5 mr-1.5" />
                Run Again
              </Button>
            )}

            {isActive && (
              <ConfirmDialog
                trigger={
                  <Button variant="destructive" size="sm" className="h-9 px-3 text-xs font-medium">
                    <RotateCcw className="h-3.5 w-3.5 mr-1.5" />
                    Cancel Execution
                  </Button>
                }
                title="Cancel this run?"
                description="This will send an immediate termination signal to the Celery worker processing this job."
                confirmLabel="Cancel Run"
                destructive
                onConfirm={() => cancelMutation.mutate()}
              />
            )}
          </div>
        }
      />

      {/* Error Callout (if run failed or has error) */}
      {isFailed && run.error && (
        <div className="rounded-xl border border-red-500/30 bg-red-500/10 dark:bg-red-950/20 p-4 text-red-900 dark:text-red-200">
          <div className="flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-red-500 shrink-0 mt-0.5" />
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold">Execution Failed</div>
              <div className="mt-1 font-mono text-xs text-red-700 dark:text-red-300 whitespace-pre-wrap break-all bg-red-950/20 p-2.5 rounded border border-red-500/20">
                {run.error}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Telemetry Strip / Metric Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {/* Card 1: Pipeline & Data Flow */}
        <Card className="border-border/80 shadow-apple-sm">
          <CardContent className="p-4 space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Data Lineage Flow
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
            <div className="pt-1">
              {pipeline ? (
                <Link
                  to={`/pipelines/${pipeline.id}`}
                  className="text-sm font-semibold text-foreground hover:underline block truncate"
                >
                  {pipeline.name}
                </Link>
              ) : (
                <span className="text-sm font-semibold text-foreground">Standalone Job</span>
              )}
              <div className="text-xs text-muted-foreground font-mono truncate mt-0.5">
                {destinationPath}
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Card 2: Ingestion Volume */}
        <Card className="border-border/80 shadow-apple-sm">
          <CardContent className="p-4 space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Ingested Volume
            </div>
            <div className="flex items-baseline gap-1.5 pt-1">
              <span className="text-2xl font-bold font-mono text-foreground tracking-tight">
                {rows != null ? rows.toLocaleString() : '—'}
              </span>
              <span className="text-xs text-muted-foreground font-mono">rows written</span>
            </div>
            <div className="text-xs text-muted-foreground font-mono flex items-center gap-1.5 pt-1">
              <Layers className="h-3 w-3" />
              <span>Format: Snappy Parquet</span>
            </div>
          </CardContent>
        </Card>

        {/* Card 3: Timing & Duration */}
        <Card className="border-border/80 shadow-apple-sm">
          <CardContent className="p-4 space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Duration & Timing
            </div>
            <div className="flex items-baseline gap-1.5 pt-1">
              <span className="text-2xl font-bold font-mono text-foreground tracking-tight">
                {formatDuration(run.started_at, run.finished_at)}
              </span>
              <span className="text-xs text-muted-foreground font-mono">elapsed</span>
            </div>
            <div className="text-xs text-muted-foreground font-mono flex items-center gap-1.5 pt-1 truncate">
              <Clock className="h-3 w-3 shrink-0" />
              <span>
                {run.started_at ? format(new Date(run.started_at), 'HH:mm:ss') : '—'}
                {' → '}
                {run.finished_at ? format(new Date(run.finished_at), 'HH:mm:ss') : isActive ? 'running' : '—'}
              </span>
            </div>
          </CardContent>
        </Card>

        {/* Card 4: Status & Schema */}
        <Card className="border-border/80 shadow-apple-sm">
          <CardContent className="p-4 space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Execution Status
            </div>
            <div className="flex items-center gap-2 pt-1">
              <StatusBadge status={run.status} />
              {run.schema_changed && <SchemaDriftBadge />}
            </div>
            <div className="text-xs text-muted-foreground flex items-center gap-1.5 pt-1">
              {run.status === 'success' ? (
                <>
                  <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                  <span>Clean execution</span>
                </>
              ) : run.status === 'failed' ? (
                <>
                  <AlertTriangle className="h-3 w-3 text-red-500" />
                  <span>Failed execution</span>
                </>
              ) : (
                <>
                  <span className="h-2 w-2 rounded-full bg-blue-500 animate-pulse" />
                  <span className="capitalize">{run.status}</span>
                </>
              )}
              <Badge variant="secondary" className="text-[10px] px-1.5 py-0 capitalize font-mono ml-auto">
                {run.triggered_by}
              </Badge>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Console Log Terminal */}
      <LogViewer runId={run.id} status={run.status} />
    </div>
  )
}
