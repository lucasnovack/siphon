import { useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { CheckCircle, Loader2, XCircle } from 'lucide-react'
import { connectionsApi } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import { ApiErrorMessage } from '@/components/shared/ApiErrorMessage'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { toast } from '@/hooks/use-toast'
import { format } from 'date-fns'

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
      setTestResult(res.data)
    } catch {
      setTestResult({ ok: false, error: 'Test failed' })
    } finally {
      setTesting(false)
    }
  }

  if (isLoading) return <div className="flex justify-center py-12"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" /></div>
  if (error || !conn) return <ApiErrorMessage error={error} />

  return (
    <div className="max-w-2xl">
      <PageHeader
        title={conn.name}
        breadcrumb={<Link to="/connections" className="hover:underline">Connections</Link>}
        action={
          <div className="flex gap-2">
            <Button variant="outline" onClick={handleTest} disabled={testing}>
              {testing ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Test'}
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
        <div className={`flex items-center gap-2 mb-4 rounded-md px-3 py-2 text-sm ${testResult.ok ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'}`}>
          {testResult.ok ? <CheckCircle className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
          {testResult.ok ? `Connected (${testResult.latency_ms}ms)` : testResult.error}
        </div>
      )}

      <Card>
        <CardHeader><CardTitle>Details</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <Row label="ID" value={conn.id} />
          <Row label="Type" value={<Badge variant="secondary">{conn.type}</Badge>} />
          <Row label="Created" value={format(new Date(conn.created_at), 'PPp')} />
          <Row label="Updated" value={format(new Date(conn.updated_at), 'PPp')} />
        </CardContent>
      </Card>
    </div>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between items-center text-sm py-1 border-b last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  )
}
