import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Database, Plus, Trash2 } from 'lucide-react'
import { connectionsApi } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { EmptyState } from '@/components/shared/EmptyState'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { toast } from '@/hooks/use-toast'

export function ConnectionsPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.connections.all,
    queryFn: () => connectionsApi.list().then((r) => r.data),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => connectionsApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.connections.all })
      toast({ title: 'Connection deleted' })
    },
    onError: () => toast({ title: 'Failed to delete connection', variant: 'destructive' }),
  })

  const connections = data ?? []

  return (
    <div>
      <PageHeader
        title="Connections"
        description="Manage your data source and destination connections"
        action={
          <Button onClick={() => navigate('/connections/new')}>
            <Plus className="h-4 w-4 mr-2" />
            New Connection
          </Button>
        }
      />

      {isLoading ? (
        <div className="flex justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
        </div>
      ) : connections.length === 0 ? (
        <EmptyState
          icon={Database}
          title="No connections yet"
          description="Add a connection to start building pipelines."
          action={{ label: 'Add Connection', onClick: () => navigate('/connections/new') }}
        />
      ) : (
        <Card className="overflow-hidden">
          <CardContent className="p-0 divide-y divide-border/60">
            {connections.map((conn) => (
              <div
                key={conn.id}
                className="flex items-center justify-between px-5 py-3.5 cursor-pointer hover:bg-foreground/[0.025] transition-colors"
                onClick={() => navigate(`/connections/${conn.id}`)}
              >
                <div className="flex items-center gap-3.5 min-w-0">
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-secondary border border-border/60 shrink-0">
                    <Database className="h-3.5 w-3.5 text-foreground/80" />
                  </div>
                  <div className="min-w-0">
                    <div className="text-xs font-medium truncate">{conn.name}</div>
                    <div className="text-[11px] text-muted-foreground tabular-nums">{conn.id.slice(0, 8)}…</div>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <Badge variant="secondary">{conn.type}</Badge>
                  <ConfirmDialog
                    trigger={
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={(e) => e.stopPropagation()}
                        className="text-muted-foreground hover:text-destructive"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    }
                    title="Delete connection?"
                    description={`This will permanently delete "${conn.name}". Pipelines using this connection will break.`}
                    confirmLabel="Delete"
                    destructive
                    onConfirm={() => deleteMutation.mutate(conn.id)}
                  />
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
