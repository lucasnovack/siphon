import { useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { connectionsApi } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { ApiErrorMessage } from '@/components/shared/ApiErrorMessage'
import { ConnectionForm } from '@/components/connections/ConnectionForm'
import { toast } from '@/hooks/use-toast'

export function ConnectionEditPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [error, setError] = useState<unknown>(null)

  const { data: conn, isLoading, error: loadError } = useQuery({
    queryKey: queryKeys.connections.detail(id!),
    queryFn: () => connectionsApi.get(id!).then((r) => r.data),
    enabled: !!id,
  })

  const mutation = useMutation({
    mutationFn: (data: unknown) => connectionsApi.update(id!, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.connections.detail(id!) })
      qc.invalidateQueries({ queryKey: queryKeys.connections.all })
      toast({ title: 'Connection updated' })
      navigate(`/connections/${id}`)
    },
    onError: (err) => setError(err),
  })

  if (isLoading) return <div className="flex justify-center py-12"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" /></div>
  if (loadError || !conn) return <ApiErrorMessage error={loadError} />

  return (
    <div className="max-w-lg">
      <PageHeader
        title={`Edit — ${conn.name}`}
        breadcrumb={
          <>
            <Link to="/connections" className="hover:underline">Connections</Link>
            {' / '}
            <Link to={`/connections/${id}`} className="hover:underline">{conn.name}</Link>
          </>
        }
      />
      <ConnectionForm
        initialValues={{ name: conn.name, type: conn.type }}
        editing
        onSubmit={async (data) => {
          setError(null)
          const payload: Record<string, unknown> = { name: data.name as string }
          // Only send config if the user filled in at least one field
          const config = data.config as Record<string, string>
          if (Object.values(config).some((v) => v !== '')) {
            payload.config = config
          }
          await mutation.mutateAsync(payload)
        }}
        submitLabel="Save Changes"
        isLoading={mutation.isPending}
        error={error}
      />
    </div>
  )
}
