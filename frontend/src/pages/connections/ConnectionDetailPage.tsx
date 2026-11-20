import { useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ArrowRight,
  CheckCircle2,
  Edit,
  GitBranch,
  KeyRound,
  Loader2,
  Lock,
  Pencil,
  ShieldCheck,
  XCircle,
} from 'lucide-react'
import { format } from 'date-fns'
import { connectionsApi, pipelinesApi } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import { ApiErrorMessage } from '@/components/shared/ApiErrorMessage'
import {
  ConnectionRoleBadge,
  EngineLogo,
  getConnectorMeta,
} from '@/components/connections/EngineLogo'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { toast } from '@/hooks/use-toast'

export function ConnectionDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [testResult, setTestResult] = useState<{ ok: boolean; latency_ms?: number; error?: string } | null>(null)
  const [testing, setTesting] = useState(false)

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
      setTestResult({ ok: false, error: 'Connection test failed — check credentials or host reachability' })
    } finally {
      setTesting(false)
    }
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
    <div className="max-w-3xl space-y-6">
      <PageHeader
        title={conn.name}
        description={`${meta.engineName} · ${meta.summary}`}
        breadcrumb={
          <Link to="/connections" className="hover:underline">
            Connections
          </Link>
        }
        action={
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={handleTest} disabled={testing}>
              {testing ? <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" /> : null}
              Test Connectivity
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
              ? `Connectivity verified${testResult.latency_ms != null ? ` (${testResult.latency_ms}ms)` : ''}`
              : testResult.error}
          </span>
        </div>
      )}

      {/* Identity & Role Overview Card */}
      <Card>
        <CardContent className="p-5 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-4">
            <EngineLogo type={conn.type} name={conn.name} size="lg" />
            <div className="space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold">{meta.engineName}</span>
                <Badge variant="secondary">{conn.type}</Badge>
                <ConnectionRoleBadge type={conn.type} />
              </div>
              <p className="text-xs text-muted-foreground">{meta.directionRule}</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Editable vs Immutable Clarifier */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
              <Pencil className="h-3.5 w-3.5 text-emerald-500" />
              Editable Properties
            </CardTitle>
            <CardDescription>Fields you can change at any time via Edit</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2.5 text-xs">
            <Row label="Connection Name" value={conn.name} />
            {meta.role === 'source' && (
              <Row
                label="Max Concurrent Jobs"
                value={`${conn.max_concurrent_jobs ?? 2} parallel runs`}
              />
            )}
            <Row
              label="Credentials (Write-Only)"
              value={
                <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
                  <KeyRound className="h-3 w-3" />
                  Rotatable (v{conn.key_version})
                </span>
              }
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
              <Lock className="h-3.5 w-3.5 text-amber-500" />
              Immutable & Security Metadata
            </CardTitle>
            <CardDescription>Fixed after creation to protect pipeline integrity</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2.5 text-xs">
            <Row label="Connector Type" value={<Badge variant="secondary">{conn.type} (locked)</Badge>} />
            <Row label="Pipeline Role" value={meta.role === 'source' ? 'Source (Read-Only)' : 'Destination (Parquet)'} />
            <Row label="Created" value={format(new Date(conn.created_at), 'MMM d, yyyy HH:mm')} />
            <Row label="Last Updated" value={format(new Date(conn.updated_at), 'MMM d, yyyy HH:mm')} />
          </CardContent>
        </Card>
      </div>

      {/* Fernet Encryption Notice */}
      <Card className="bg-secondary/30 border-border/70">
        <CardContent className="p-4 flex items-start gap-3">
          <ShieldCheck className="h-4 w-4 text-emerald-500 shrink-0 mt-0.5" />
          <div className="space-y-0.5 text-xs">
            <div className="font-medium">Credentials Encrypted at Rest (Fernet AES-128 · Key v{conn.key_version})</div>
            <p className="text-muted-foreground text-[11.5px]">
              For security, stored passwords, secret keys, and Service Account JSONs are never returned by the API. When editing this connection, you can rename it or adjust concurrency without re-entering credentials, or optionally rotate the secret.
            </p>
          </div>
        </CardContent>
      </Card>

      {/* Linked Pipelines */}
      <Card className="overflow-hidden">
        <CardHeader className="border-b border-border/60 py-4">
          <CardTitle className="flex items-center justify-between text-xs uppercase tracking-wider text-muted-foreground">
            <span className="flex items-center gap-2">
              <GitBranch className="h-3.5 w-3.5" />
              Pipelines Using This Connection ({linkedPipelines.length})
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {linkedPipelines.length === 0 ? (
            <p className="py-8 text-center text-xs text-muted-foreground">
              No pipelines are linked to this connection yet.
            </p>
          ) : (
            <div className="divide-y divide-border/60">
              {linkedPipelines.map((p) => {
                const usedAs = p.source_connection_id === conn.id ? 'Source (Read)' : 'Destination (Parquet)'
                return (
                  <Link
                    key={p.id}
                    to={`/pipelines/${p.id}`}
                    className="flex items-center justify-between px-5 py-3.5 hover:bg-foreground/[0.025] transition-colors"
                  >
                    <div className="space-y-0.5">
                      <div className="text-xs font-medium flex items-center gap-2">
                        <span>{p.name}</span>
                        <Badge variant="outline" className="text-[10px]">
                          Used as {usedAs}
                        </Badge>
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        {p.extraction_mode} · {p.destination_path}
                      </div>
                    </div>
                    <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
                  </Link>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>
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
