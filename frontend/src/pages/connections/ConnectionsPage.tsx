import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Database, Edit, KeyRound, Layers, Plus, Trash2 } from 'lucide-react'
import { connectionsApi, type Connection } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { EmptyState } from '@/components/shared/EmptyState'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import {
  ConnectionRoleBadge,
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

export function ConnectionsPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [roleFilter, setRoleFilter] = useState<RoleFilter>('all')

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
  const sources = connections.filter((c) => getConnectorMeta(c.type, c.name).role === 'source')
  const destinations = connections.filter((c) => getConnectorMeta(c.type, c.name).role === 'destination')

  const showSources = roleFilter === 'all' || roleFilter === 'source'
  const showDestinations = roleFilter === 'all' || roleFilter === 'destination'

  return (
    <div className="space-y-6">
      <PageHeader
        title="Connections"
        description="Configure read-only extraction sources and Bronze-layer Parquet destinations"
        action={
          <Button onClick={() => navigate('/connections/new')}>
            <Plus className="h-3.5 w-3.5 mr-1.5" />
            New Connection
          </Button>
        }
      />

      {/* Unidirectional Architecture Callout */}
      <Card className="bg-secondary/35 border-border/70">
        <CardContent className="p-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div className="space-y-1">
            <div className="text-xs font-semibold tracking-tight flex items-center gap-2">
              <span>Unidirectional Bronze Ingestion Flow</span>
              <Badge variant="outline" className="text-[10px] py-0">No Reverse ETL</Badge>
            </div>
            <p className="text-[11.5px] text-muted-foreground">
              Sources (<span className="text-foreground font-medium">SQL, SFTP</span>) are strictly{' '}
              <span className="text-foreground font-medium">read-only</span>. Siphon extracts batches and writes Snappy Parquet exclusively to Destinations (
              <span className="text-foreground font-medium">S3, MinIO, GCS</span>).
            </p>
          </div>

          <div className="flex items-center gap-2 text-[11px] shrink-0 bg-card/90 border border-border/70 rounded-lg px-3 py-2 shadow-apple-sm">
            <span className="font-medium text-sky-600 dark:text-sky-300">Source (Read)</span>
            <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="text-muted-foreground">Siphon</span>
            <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="font-medium text-emerald-600 dark:text-emerald-300">Destination (Parquet)</span>
          </div>
        </CardContent>
      </Card>

      {/* Filter Segmented Control */}
      {connections.length > 0 && (
        <div className="flex items-center justify-between">
          <div
            role="tablist"
            aria-label="Filter connections by role"
            className="inline-flex items-center gap-1 p-1 rounded-lg bg-secondary/80 border border-border/60"
          >
            {(
              [
                { id: 'all', label: `All (${connections.length})` },
                { id: 'source', label: `Sources · Read (${sources.length})` },
                { id: 'destination', label: `Destinations · Parquet (${destinations.length})` },
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
                    'px-3 py-1 rounded-md text-xs transition-all duration-150',
                    active
                      ? 'bg-card text-foreground font-medium shadow-apple-sm'
                      : 'text-muted-foreground hover:text-foreground',
                  )}
                >
                  {tab.label}
                </button>
              )
            })}
          </div>
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
          description="Add a SQL/SFTP source and an S3/GCS destination to start building Bronze pipelines."
          action={{ label: 'Add Connection', onClick: () => navigate('/connections/new') }}
        />
      ) : (
        <div className="space-y-8">
          {showSources && (
            <ConnectionGroupSection
              title="Sources — Read-Only Data Extraction"
              subtitle="Databases and SFTP servers from which Siphon extracts rows or files. Cannot be used as pipeline destinations."
              items={sources}
              emptyText="No source connections configured yet."
              onSelect={(id) => navigate(`/connections/${id}`)}
              onEdit={(id) => navigate(`/connections/${id}/edit`)}
              onDelete={(id) => deleteMutation.mutate(id)}
            />
          )}

          {showDestinations && (
            <ConnectionGroupSection
              title="Destinations — Bronze Layer Object Storage"
              subtitle="S3, MinIO, and Google Cloud Storage buckets where Siphon writes partitioned Parquet datasets."
              items={destinations}
              emptyText="No destination connections configured yet."
              onSelect={(id) => navigate(`/connections/${id}`)}
              onEdit={(id) => navigate(`/connections/${id}/edit`)}
              onDelete={(id) => deleteMutation.mutate(id)}
            />
          )}
        </div>
      )}
    </div>
  )
}

function ConnectionGroupSection({
  title,
  subtitle,
  items,
  emptyText,
  onSelect,
  onEdit,
  onDelete,
}: {
  title: string
  subtitle: string
  items: Connection[]
  emptyText: string
  onSelect: (id: string) => void
  onEdit: (id: string) => void
  onDelete: (id: string) => void
}) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-xs font-semibold uppercase tracking-wider text-foreground/90">{title}</h2>
        <p className="text-[11.5px] text-muted-foreground mt-0.5">{subtitle}</p>
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
              return (
                <div
                  key={conn.id}
                  className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between px-5 py-3.5 cursor-pointer hover:bg-foreground/[0.025] transition-colors"
                  onClick={() => onSelect(conn.id)}
                >
                  <div className="flex items-center gap-3.5 min-w-0">
                    <EngineLogo type={conn.type} name={conn.name} size="md" />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs font-semibold truncate">{conn.name}</span>
                        <Badge variant="secondary" className="text-[10.5px]">
                          {meta.engineName}
                        </Badge>
                        <ConnectionRoleBadge type={conn.type} />
                      </div>
                      <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground mt-1 tabular-nums">
                        <span>id: {conn.id.slice(0, 8)}…</span>
                        {meta.role === 'source' && conn.max_concurrent_jobs != null && (
                          <span className="inline-flex items-center gap-1">
                            <Layers className="h-3 w-3" />
                            max {conn.max_concurrent_jobs} concurrent {conn.max_concurrent_jobs === 1 ? 'job' : 'jobs'}
                          </span>
                        )}
                        <span className="inline-flex items-center gap-1">
                          <KeyRound className="h-3 w-3" />
                          encrypted key v{conn.key_version}
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-1.5 shrink-0">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation()
                        onEdit(conn.id)
                      }}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <Edit className="h-3.5 w-3.5 mr-1" />
                      Edit
                    </Button>
                    <ConfirmDialog
                      trigger={
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={(e) => e.stopPropagation()}
                          className="text-muted-foreground hover:text-destructive"
                          aria-label={`Delete ${conn.name}`}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      }
                      title="Delete connection?"
                      description={`This will permanently delete "${conn.name}". Pipelines using this connection will break.`}
                      confirmLabel="Delete"
                      destructive
                      onConfirm={() => onDelete(conn.id)}
                    />
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
