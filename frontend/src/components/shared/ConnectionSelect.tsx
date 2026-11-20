import { useQuery } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { connectionsApi, type Connection } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { getConnectorMeta } from '@/components/connections/EngineLogo'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Button } from '@/components/ui/button'

interface ConnectionSelectProps {
  value: string
  onChange: (value: string) => void
  filterType?: Connection['type'] | Connection['type'][]
  onCreateNew?: () => void
  placeholder?: string
  error?: string
}

export function ConnectionSelect({
  value,
  onChange,
  filterType,
  onCreateNew,
  placeholder = 'Select connection',
  error,
}: ConnectionSelectProps) {
  const { data } = useQuery({
    queryKey: queryKeys.connections.list(1, 200),
    queryFn: () => connectionsApi.list().then((r) => r.data),
  })

  const allowedTypes = filterType
    ? (Array.isArray(filterType) ? filterType : [filterType])
    : null

  const options = allowedTypes
    ? (data ?? []).filter((c) => allowedTypes.includes(c.type))
    : (data ?? [])

  return (
    <div className="space-y-1">
      <div className="flex gap-2">
        <Select value={value} onValueChange={onChange}>
          <SelectTrigger className={error ? 'border-destructive flex-1' : 'flex-1'}>
            <SelectValue placeholder={placeholder} />
          </SelectTrigger>
          <SelectContent>
            {options.map((c) => {
              const meta = getConnectorMeta(c.type, c.name)
              return (
                <SelectItem key={c.id} value={c.id}>
                  <span className="font-medium">{c.name}</span>
                  <span className="ml-2 text-[11px] text-muted-foreground">
                    {meta.engineName} · {meta.role === 'source' ? 'Source' : 'Destination'}
                  </span>
                </SelectItem>
              )
            })}
            {options.length === 0 && (
              <div className="p-2 text-sm text-muted-foreground text-center">No connections found</div>
            )}
          </SelectContent>
        </Select>
        {onCreateNew && (
          <Button type="button" variant="outline" size="icon" onClick={onCreateNew}>
            <Plus className="h-4 w-4" />
          </Button>
        )}
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  )
}
