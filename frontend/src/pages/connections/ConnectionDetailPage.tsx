import { useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ArrowRight,
  Check,
  CheckCircle2,
  Copy,
  Edit,
  GitBranch,
  Loader2,
  XCircle,
} from 'lucide-react'
import { format } from 'date-fns'
import { connectionsApi, pipelinesApi } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import { ApiErrorMessage } from '@/components/shared/ApiErrorMessage'
import { EngineLogo, getConnectorMeta } from '@/components/connections/EngineLogo'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { toast } from '@/hooks/use-toast'

export function ConnectionDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [testResult, setTestResult] = useState<{ ok: boolean; latency_ms?: number; error?: string } | null>(null)
  const [testing, setTesting] = useState(false)
  const [copied, setCopied] = useState(false)

  const { data: conn, isLoading, error } = useQuery({
    queryKey: queryKeys.connections.detail(id!),
    queryFn: () => connectionsApi.get(id!).then((r) => r.data),
    enabled: !!id,
  })

  const { data: pipelines } = useQuery({
    queryKey: queryKeys.pipelines.all,
    queryFn: () => pipelinesApi.list().then((r) => r.data),
    enabled: !!id,
  })

  const deleteMutation = useMutation({
    mutationFn: () => connectionsApi.delete(id!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.connections.all })
      toast({ title: 'Connection deleted' })
      navigate('/connections')
    },
  })

  async function handleTest() {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await connectionsApi.testById(id!)
      setTestResult({
        ok: res.data.ok ?? true,
        latency_ms: res.data.latency_ms,
      })
    } catch {
      setTestResult({ ok: false, error: 'Connection test failed' })
    } finally {
      setTesting(false)
    }
  }

  function handleCopyId() {
    if (!conn) return
    navigator.clipboard.writeText(conn.id)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  if (isLoading) {
    return (
      <div className="flex justify-center py-12">
        <div className="animate-spin rounded-full h-7 w-7 border-b-2 border-primary" />
      </div>
    )
  }
  if (error || !conn) return <ApiErrorMessage error={error} />

  const meta = getConnectorMeta(conn.type, conn.name)
  const linkedPipelines = (pipelines ?? []).filter(
    (p) => p.source_connection_id === conn.id || p.dest_connection_id === conn.id,
  )

  return (
    <div className="max-w-2xl space-y-6">
      <PageHeader
        title={conn.name}
        description={`${meta.engineName} · ${meta.role === 'source' ? 'Source' : 'Destination'}`}
        breadcrumb={
          <Link to="/connections" className="hover:underline">
            Connections
          </Link>
        }
        action={
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={handleTest} disabled={testing}>
              {testing ? (
                <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
              ) : (
                <CheckCircle2 className="h-3.5 w-3.5 mr-1.5 text-muted-foreground" />
              )}
              Test
            </Button>
            <Button variant="outline" onClick={() => navigate(`/connections/${id}/edit`)}>
              <Edit className="h-3.5 w-3.5 mr-1.5" />
              Edit
            </Button>
            <ConfirmDialog
              trigger={<Button variant="destructive">Delete</Button>}
              title="Delete connection?"
              description={`Permanently delete "${conn.name}"? Pipelines using it will break.`}
              confirmLabel="Delete"
              destructive
              onConfirm={() => deleteMutation.mutate()}
            />
          </div>
        }
      />

      {testResult && (
        <div
          className={`flex items-center gap-2.5 rounded-xl border px-4 py-3 text-xs ${
            testResult.ok
              ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
              : 'border-rose-500/25 bg-rose-500/10 text-rose-700 dark:text-rose-300'
          }`}
        >
          {testResult.ok ? <CheckCircle2 className="h-4 w-4 shrink-0" /> : <XCircle className="h-4 w-4 shrink-0" />}
          <span>
            {testResult.ok
              ? `Connected${testResult.latency_ms != null ? ` (${testResult.latency_ms}ms)` : ''}`
              : testResult.error}
          </span>
        </div>
      )}

      <Card>
        <CardHeader className="flex flex-row items-center gap-3.5 border-b border-border/60 py-4">
          <EngineLogo type={conn.type} name={conn.name} size="md" />
          <div>
            <CardTitle className="text-sm font-semibold">{conn.name}</CardTitle>
            <p className="text-xs text-muted-foreground mt-0.5">
              {meta.engineName} · {meta.role === 'source' ? 'Extraction Source' : 'Bronze Destination'}
            </p>
          </div>
        </CardHeader>
        <CardContent className="p-5 space-y-2.5 text-xs">
          <Row
            label="ID"
            value={
              <button
                type="button"
                onClick={handleCopyId}
                className="inline-flex items-center gap-1.5 text-muted-foreground hover:text-foreground transition-colors"
                title="Copy connection UUID"
              >
                <span className="tabular-nums">{conn.id}</span>
                {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
              </button>
            }
          />
          <Row
            label="Engine"
            value={
              <div className="flex items-center gap-1.5">
                <Badge variant="secondary">{meta.engineName}</Badge>
                {meta.formatBadge && <Badge variant="secondary">{meta.formatBadge}</Badge>}
              </div>
            }
          />
          {meta.role === 'source' && (
            <Row label="Concurrency Limit" value={`max ${conn.max_concurrent_jobs ?? 2} concurrent jobs`} />
          )}
          <Row label="Credentials" value={`Encrypted (key v${conn.key_version})`} />
          <Row label="Created" value={format(new Date(conn.created_at), 'MMM d, yyyy HH:mm')} />
          <Row label="Updated" value={format(new Date(conn.updated_at), 'MMM d, yyyy HH:mm')} />
        </CardContent>
      </Card>

      {linkedPipelines.length > 0 && (
        <Card className="overflow-hidden">
          <CardHeader className="border-b border-border/60 py-3.5">
            <CardTitle className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
              <GitBranch className="h-3.5 w-3.5" />
              Linked Pipelines ({linkedPipelines.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0 divide-y divide-border/60">
            {linkedPipelines.map((p) => (
              <Link
                key={p.id}
                to={`/pipelines/${p.id}`}
                className="flex items-center justify-between px-5 py-3.5 hover:bg-foreground/[0.02] transition-colors"
              >
                <div>
                  <div className="text-xs font-medium">{p.name}</div>
                  <div className="text-[11px] text-muted-foreground mt-0.5">
                    {p.extraction_mode} · {p.destination_path}
                  </div>
                </div>
                <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
              </Link>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between items-center py-1.5 border-b border-border/50 last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium text-right">{value}</span>
    </div>
  )
}
