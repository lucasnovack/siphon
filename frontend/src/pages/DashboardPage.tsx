import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Activity, ArrowUpRight, CheckCircle2, GitBranch, XCircle } from 'lucide-react'
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
      <PageHeader title="Dashboard" description="Overview of your Bronze-layer pipelines" />

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground flex items-center justify-between">
              <span>Active Pipelines</span>
              <GitBranch className="h-3.5 w-3.5 text-muted-foreground/70" />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-semibold tracking-tight tabular-nums">{activePipelines}</div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground flex items-center justify-between">
              <span>Successful Runs</span>
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-semibold tracking-tight tabular-nums text-emerald-600 dark:text-emerald-400">
              {successToday}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground flex items-center justify-between">
              <span>Failed Runs</span>
              <XCircle className="h-3.5 w-3.5 text-rose-500" />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-semibold tracking-tight tabular-nums text-rose-600 dark:text-rose-400">
              {failedToday}
            </div>
          </CardContent>
        </Card>
      </div>

      <Card className="overflow-hidden">
        <CardHeader className="flex flex-row items-center justify-between border-b border-border/60 py-4">
          <CardTitle className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <Activity className="h-3.5 w-3.5" /> Recent Runs
          </CardTitle>
          <Link
            to="/runs"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            View all
            <ArrowUpRight className="h-3.5 w-3.5" />
          </Link>
        </CardHeader>
        <CardContent className="p-0">
          {recentRuns.length === 0 ? (
            <p className="text-xs text-muted-foreground py-10 text-center">No runs yet.</p>
          ) : (
            <div className="divide-y divide-border/60">
              {recentRuns.map((run) => (
                <div
                  key={run.id}
                  className="flex items-center justify-between px-5 py-3.5 hover:bg-foreground/[0.025] transition-colors"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <Link
                        to={`/runs/${run.id}`}
                        className="text-xs font-medium text-foreground hover:underline underline-offset-4"
                      >
                        {run.id.slice(0, 8)}…
                      </Link>
                      {run.pipeline_id && (
                        <Link
                          to={`/pipelines/${run.pipeline_id}`}
                          className="text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                        >
                          · pipeline
                        </Link>
                      )}
                    </div>
                    <div className="text-[11px] text-muted-foreground mt-0.5 tabular-nums">
                      {run.triggered_by} · {run.created_at ? format(new Date(run.created_at), 'MMM d, HH:mm') : '—'}
                    </div>
                  </div>
                  <StatusBadge status={run.status} />
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
