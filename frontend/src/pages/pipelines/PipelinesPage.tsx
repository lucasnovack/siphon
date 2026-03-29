import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { GitBranch, Play, Plus, Trash2 } from 'lucide-react'
import { pipelinesApi } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { EmptyState } from '@/components/shared/EmptyState'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { toast } from '@/hooks/use-toast'

export function PipelinesPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [page] = useState(1)

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.pipelines.list(page, 50),
    queryFn: () => pipelinesApi.list(page, 50).then((r) => r.data),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => pipelinesApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.pipelines.all })
      toast({ title: 'Pipeline deleted' })
    },
    onError: () => toast({ title: 'Failed to delete pipeline', variant: 'destructive' }),
  })

  const triggerMutation = useMutation({
    mutationFn: (id: string) => pipelinesApi.trigger(id),
    onSuccess: (res) => {
      toast({ title: 'Pipeline triggered', description: `Job ${res.data.job_id.slice(0, 8)}…` })
      navigate(`/runs/${res.data.job_id}`)
    },
    onError: () => toast({ title: 'Failed to trigger pipeline', variant: 'destructive' }),
  })

  const pipelines = data?.items ?? []

  return (
    <div>
      <PageHeader
        title="Pipelines"
        description="Manage and monitor your extraction pipelines"
        action={
          <Button onClick={() => navigate('/pipelines/new')}>
            <Plus className="h-4 w-4 mr-2" />
            New Pipeline
          </Button>
        }
      />

      {isLoading ? (
        <div className="flex justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
        </div>
      ) : pipelines.length === 0 ? (
        <EmptyState
          icon={GitBranch}
          title="No pipelines yet"
          description="Create a pipeline to start extracting data."
          action={{ label: 'Create Pipeline', onClick: () => navigate('/pipelines/new') }}
        />
      ) : (
        <div className="grid gap-3">
          {pipelines.map((pipeline) => (
            <Card
              key={pipeline.id}
              className="cursor-pointer hover:shadow-md transition-shadow"
              onClick={() => navigate(`/pipelines/${pipeline.id}`)}
            >
              <CardContent className="flex items-center justify-between p-4">
                <div className="flex items-center gap-3">
                  <div className="rounded-md bg-primary/10 p-2">
                    <GitBranch className="h-4 w-4 text-primary" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{pipeline.name}</span>
                      {!pipeline.is_active && <Badge variant="secondary">Inactive</Badge>}
                    </div>
                    <div className="text-xs text-muted-foreground mt-0.5">
                      {pipeline.extraction_mode}
                      {pipeline.schedule && <> · <span className="font-mono">{pipeline.schedule.cron_expr}</span></>}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={(e) => { e.stopPropagation(); triggerMutation.mutate(pipeline.id) }}
                    disabled={triggerMutation.isPending}
                  >
                    <Play className="h-3 w-3 mr-1" />
                    Run
                  </Button>
                  <ConfirmDialog
                    trigger={
                      <Button variant="ghost" size="icon" onClick={(e) => e.stopPropagation()}>
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    }
                    title="Delete pipeline?"
                    description={`Permanently delete "${pipeline.name}"?`}
                    confirmLabel="Delete"
                    destructive
                    onConfirm={() => deleteMutation.mutate(pipeline.id)}
                  />
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
