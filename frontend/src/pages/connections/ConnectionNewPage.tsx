import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { connectionsApi } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { ConnectionForm } from '@/components/connections/ConnectionForm'
import { toast } from '@/hooks/use-toast'

export function ConnectionNewPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [error, setError] = useState<unknown>(null)

  const mutation = useMutation({
    mutationFn: (data: unknown) => connectionsApi.create(data),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: queryKeys.connections.all })
      toast({ title: 'Connection created' })
      navigate(`/connections/${res.data.id}`)
    },
    onError: (err) => setError(err),
  })

  return (
    <div className="max-w-lg">
      <PageHeader
        title="New Connection"
        breadcrumb={<span>Connections / New</span>}
      />
      <ConnectionForm
        onSubmit={async (data) => {
          setError(null)
          await mutation.mutateAsync(data)
        }}
        isLoading={mutation.isPending}
        error={error}
      />
    </div>
  )
}
