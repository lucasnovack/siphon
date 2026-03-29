import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { PageHeader } from '@/components/shared/PageHeader'
import { ApiErrorMessage } from '@/components/shared/ApiErrorMessage'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'

interface SystemInfo {
  version: string
  environment: string
  database_url?: string
  scheduler_running?: boolean
  worker_concurrency?: number
  queue_depth?: number
  [key: string]: unknown
}

function useSystemInfo() {
  return useQuery({
    queryKey: ['system', 'info'],
    queryFn: () => api.get<SystemInfo>('/api/v1/system/info').then((r) => r.data),
    retry: false,
  })
}

export function SystemPage() {
  const { data, isLoading, error } = useSystemInfo()

  return (
    <div className="max-w-2xl space-y-6">
      <PageHeader title="System" />

      {isLoading && <div className="flex justify-center py-12"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" /></div>}

      {error && (
        <Card>
          <CardContent className="py-6">
            <p className="text-sm text-muted-foreground mb-2">System info endpoint not available.</p>
            <ApiErrorMessage error={error} />
          </CardContent>
        </Card>
      )}

      {data && (
        <Card>
          <CardHeader><CardTitle>Runtime Info</CardTitle></CardHeader>
          <CardContent className="space-y-0 divide-y text-sm">
            <InfoRow label="Version"><code className="font-mono">{data.version ?? '—'}</code></InfoRow>
            <InfoRow label="Environment">
              <Badge variant={data.environment === 'production' ? 'default' : 'secondary'}>
                {data.environment ?? '—'}
              </Badge>
            </InfoRow>
            {data.scheduler_running != null && (
              <InfoRow label="Scheduler">
                <Badge variant={data.scheduler_running ? 'success' : 'destructive'}>
                  {data.scheduler_running ? 'running' : 'stopped'}
                </Badge>
              </InfoRow>
            )}
            {data.worker_concurrency != null && (
              <InfoRow label="Worker Concurrency">{data.worker_concurrency}</InfoRow>
            )}
            {data.queue_depth != null && (
              <InfoRow label="Queue Depth">{data.queue_depth}</InfoRow>
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader><CardTitle>API</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p className="text-muted-foreground">
            Base URL: <code className="font-mono">{window.location.origin}/api/v1</code>
          </p>
          <p className="text-muted-foreground">
            Docs: <a href="/docs" target="_blank" rel="noreferrer" className="text-primary hover:underline">/docs</a>
            {' · '}
            <a href="/redoc" target="_blank" rel="noreferrer" className="text-primary hover:underline">/redoc</a>
          </p>
        </CardContent>
      </Card>
    </div>
  )
}

function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between items-center py-2">
      <span className="text-muted-foreground">{label}</span>
      <span>{children}</span>
    </div>
  )
}
