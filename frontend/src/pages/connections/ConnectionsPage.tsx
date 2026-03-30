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
        <div className="grid gap-3">
          {connections.map((conn) => (
            <Card
              key={conn.id}
              className="cursor-pointer hover:shadow-md transition-shadow"
              onClick={() => navigate(`/connections/${conn.id}`)}
            >
              <CardContent className="flex items-center justify-between p-4">
                <div className="flex items-center gap-3">
                  <div className="rounded-md bg-primary/10 p-2">
                    <Database className="h-4 w-4 text-primary" />
                  </div>
                  <div>
                    <div className="font-medium">{conn.name}</div>
                    <div className="text-xs text-muted-foreground">{conn.id.slice(0, 8)}…</div>
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
                      >
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    }
                    title="Delete connection?"
                    description={`This will permanently delete "${conn.name}". Pipelines using this connection will break.`}
                    confirmLabel="Delete"
                    destructive
                    onConfirm={() => deleteMutation.mutate(conn.id)}
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
