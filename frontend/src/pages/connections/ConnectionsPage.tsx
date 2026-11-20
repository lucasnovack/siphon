import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { CheckCircle2, Database, Edit, Loader2, Plus, Trash2 } from 'lucide-react'
import { connectionsApi, type Connection } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { EmptyState } from '@/components/shared/EmptyState'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import {
  EngineLogo,
  getConnectorMeta,
  type ConnectorRole,
} from '@/components/connections/EngineLogo'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { toast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'

type RoleFilter = 'all' | ConnectorRole
type TestStatus = { state: 'idle' | 'testing' | 'ok' | 'error'; latency_ms?: number }

export function ConnectionsPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [roleFilter, setRoleFilter] = useState<RoleFilter>('all')
  const [testStates, setTestStates] = useState<Record<string, TestStatus>>({})

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

  async function handleInlineTest(conn: Connection) {
    setTestStates((prev) => ({ ...prev, [conn.id]: { state: 'testing' } }))
    try {
      const res = await connectionsApi.testById(conn.id)
      setTestStates((prev) => ({
        ...prev,
        [conn.id]: { state: 'ok', latency_ms: res.data.latency_ms },
      }))
    } catch {
      setTestStates((prev) => ({ ...prev, [conn.id]: { state: 'error' } }))
      toast({ title: `Connection test failed for "${conn.name}"`, variant: 'destructive' })
    }
  }

  const connections = data ?? []
  const sources = connections.filter((c) => getConnectorMeta(c.type, c.name).role === 'source')
  const destinations = connections.filter((c) => getConnectorMeta(c.type, c.name).role === 'destination')

  const showSources = roleFilter === 'all' || roleFilter === 'source'
  const showDestinations = roleFilter === 'all' || roleFilter === 'destination'

  return (
    <div className="space-y-6">
      <PageHeader
        title="Connections"
        description="Manage extraction sources and Bronze storage targets."
        action={
          <Button onClick={() => navigate('/connections/new')}>
            <Plus className="h-3.5 w-3.5 mr-1.5" />
            New Connection
          </Button>
        }
      />

      {connections.length > 0 && (
        <div
          role="tablist"
          aria-label="Filter connections"
          className="inline-flex items-center gap-1 p-1 rounded-xl bg-secondary/80 border border-border/60"
        >
          {(
            [
              { id: 'all', label: `All (${connections.length})` },
              { id: 'source', label: `Sources (${sources.length})` },
              { id: 'destination', label: `Destinations (${destinations.length})` },
            ] as const
          ).map((tab) => {
            const active = roleFilter === tab.id
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setRoleFilter(tab.id)}
                className={cn(
                  'px-3.5 py-1.5 rounded-lg text-xs transition-all duration-150',
                  active
                    ? 'bg-card text-foreground font-semibold shadow-apple-sm'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {tab.label}
              </button>
            )
          })}
        </div>
      )}

      {isLoading ? (
        <div className="flex justify-center py-12">
          <div className="animate-spin rounded-full h-7 w-7 border-b-2 border-primary" />
        </div>
      ) : connections.length === 0 ? (
        <EmptyState
          icon={Database}
          title="No connections yet"
          description="Add a source and a Bronze destination to start building pipelines."
          action={{ label: 'Add Connection', onClick: () => navigate('/connections/new') }}
        />
      ) : (
        <div className="space-y-7">
          {showSources && (
            <ConnectionGroupSection
              title={`SOURCES (${sources.length})`}
              items={sources}
              emptyText="No sources configured."
              testStates={testStates}
              onSelect={(id) => navigate(`/connections/${id}`)}
              onEdit={(id) => navigate(`/connections/${id}/edit`)}
              onDelete={(id) => deleteMutation.mutate(id)}
              onTest={handleInlineTest}
            />
          )}

          {showDestinations && (
            <ConnectionGroupSection
              title={`DESTINATIONS (${destinations.length})`}
              items={destinations}
              emptyText="No destinations configured."
              testStates={testStates}
              onSelect={(id) => navigate(`/connections/${id}`)}
              onEdit={(id) => navigate(`/connections/${id}/edit`)}
              onDelete={(id) => deleteMutation.mutate(id)}
              onTest={handleInlineTest}
            />
          )}
        </div>
      )}
    </div>
  )
}

function ConnectionGroupSection({
  title,
  items,
  emptyText,
  testStates,
  onSelect,
  onEdit,
  onDelete,
  onTest,
}: {
  title: string
  items: Connection[]
  emptyText: string
  testStates: Record<string, TestStatus>
  onSelect: (id: string) => void
  onEdit: (id: string) => void
  onDelete: (id: string) => void
  onTest: (conn: Connection) => void
}) {
  return (
    <section className="space-y-3">
      <div className="border-b border-border/60 pb-2">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
          {title}
        </h2>
      </div>

      {items.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="py-6 text-center text-xs text-muted-foreground">{emptyText}</CardContent>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <CardContent className="p-0 divide-y divide-border/60">
            {items.map((conn) => {
              const meta = getConnectorMeta(conn.type, conn.name)
              const status = testStates[conn.id]?.state ?? 'idle'
              const latency = testStates[conn.id]?.latency_ms
              const isError = status === 'error'

              return (
                <div
                  key={conn.id}
                  className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between px-5 py-4 cursor-pointer hover:bg-foreground/[0.02] transition-colors"
                  onClick={() => onSelect(conn.id)}
                >
                  <div className="flex items-center gap-3.5 min-w-0">
                    <EngineLogo type={conn.type} name={conn.name} size="md" />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[13px] font-semibold text-foreground truncate">
                          {conn.name}
                        </span>
                        <Badge variant="secondary" className="text-[11px] font-medium">
                          {meta.engineName}
                        </Badge>
                        {meta.formatBadge && (
                          <Badge variant="secondary" className="text-[11px] font-medium">
                            {meta.formatBadge}
                          </Badge>
                        )}
                      </div>

                      <div className="flex flex-wrap items-center gap-2 text-[11.5px] text-muted-foreground mt-1 tabular-nums">
                        <span className="inline-flex items-center gap-1.5">
                          <span
                            className={cn(
                              'h-2 w-2 rounded-full',
                              isError ? 'bg-rose-500' : 'bg-emerald-500',
                            )}
                          />
                          <span className={cn(isError && 'text-rose-600 dark:text-rose-400')}>
                            {isError
                              ? 'Connection failed'
                              : latency != null
                                ? `Connected (${latency}ms)`
                                : 'Connected'}
                          </span>
                        </span>

                        {meta.role === 'source' && conn.max_concurrent_jobs != null && (
                          <>
                            <span>•</span>
                            <span>
                              max {conn.max_concurrent_jobs} concurrent{' '}
                              {conn.max_concurrent_jobs === 1 ? 'job' : 'jobs'}
                            </span>
                          </>
                        )}

                        <span>•</span>
                        <span>encrypted key v{conn.key_version}</span>
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-1.5 shrink-0" onClick={(e) => e.stopPropagation()}>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => onEdit(conn.id)}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <Edit className="h-3.5 w-3.5 mr-1.5" />
                      Edit
                    </Button>

                    <ConfirmDialog
                      trigger={
                        <Button
                          variant="ghost"
                          size="icon"
                          className="text-muted-foreground hover:text-destructive"
                          aria-label={`Delete ${conn.name}`}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      }
                      title="Delete connection?"
                      description={`Permanently delete "${conn.name}"? Pipelines using this connection will break.`}
                      confirmLabel="Delete"
                      destructive
                      onConfirm={() => onDelete(conn.id)}
                    />

                    <Button
                      variant="outline"
                      size="sm"
                      disabled={status === 'testing'}
                      onClick={() => onTest(conn)}
                      className="ml-1 rounded-lg"
                    >
                      {status === 'testing' ? (
                        <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                      ) : (
                        <CheckCircle2 className="h-3.5 w-3.5 mr-1.5 text-muted-foreground" />
                      )}
                      Test
                    </Button>
                  </div>
                </div>
              )
            })}
          </CardContent>
        </Card>
      )}
    </section>
  )
}
