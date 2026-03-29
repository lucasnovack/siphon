import { AlertTriangle } from 'lucide-react'
import { Badge } from '@/components/ui/badge'

export function SchemaDriftBadge() {
  return (
    <Badge variant="warning" className="gap-1">
      <AlertTriangle className="h-3 w-3" />
      Schema Changed
    </Badge>
  )
}
