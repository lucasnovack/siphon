import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Activity, CheckCircle, GitBranch, XCircle } from 'lucide-react'
import { format } from 'date-fns'
import { runsApi, pipelinesApi } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

export function DashboardPage() {
  const { data: runs } = useQuery({
    queryKey: queryKeys.runs.list({ limit: 10 }),
    queryFn: () => runsApi.list({ limit: 10 }).then((r) => r.data),
    refetchInterval: 10_000,
    refetchIntervalInBackground: false,
  })

  const { data: pipelines } = useQuery({
    queryKey: queryKeys.pipelines.list(1, 100),
    queryFn: () => pipelinesApi.list().then((r) => r.data),
  })

  const recentRuns = runs ?? []
  const activePipelines = pipelines?.filter((p) => p.is_active).length ?? 0
  const successToday = recentRuns.filter((r) => r.status === 'success').length
  const failedToday = recentRuns.filter((r) => r.status === 'failed').length

  return (
    <div>
      <PageHeader title="Dashboard" description="Overview of your data pipelines" />

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
              <GitBranch className="h-4 w-4" /> Active Pipelines
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">{activePipelines}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
              <CheckCircle className="h-4 w-4 text-green-500" /> Successful Runs
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold text-green-600">{successToday}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
              <XCircle className="h-4 w-4 text-red-500" /> Failed Runs
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold text-red-600">{failedToday}</div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5" /> Recent Runs
          </CardTitle>
        </CardHeader>
        <CardContent>
          {recentRuns.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4 text-center">No runs yet.</p>
          ) : (
            <div className="divide-y">
              {recentRuns.map((run) => (
                <div key={run.id} className="flex items-center justify-between py-3">
                  <div>
                    <Link
                      to={`/runs/${run.id}`}
                      className="text-sm font-medium hover:underline text-primary"
                    >
                      {run.id.slice(0, 8)}…
                    </Link>
                    {run.pipeline_id && (
                      <Link
                        to={`/pipelines/${run.pipeline_id}`}
                        className="ml-2 text-xs text-muted-foreground hover:underline"
                      >
                        pipeline
                      </Link>
                    )}
                    <div className="text-xs text-muted-foreground mt-0.5">
                      {run.triggered_by} · {run.created_at ? format(new Date(run.created_at), 'MMM d, HH:mm') : '—'}
                    </div>
                  </div>
                  <StatusBadge status={run.status} />
                </div>
              ))}
            </div>
          )}
          <div className="pt-3">
            <Link to="/runs" className="text-sm text-primary hover:underline">
              View all runs →
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
