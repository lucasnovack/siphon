import { useState } from 'react'
import cronstrue from 'cronstrue'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface CronInputProps {
  value: string
  onChange: (value: string) => void
  error?: string
}

export function CronInput({ value, onChange, error }: CronInputProps) {
  const [humanReadable, setHumanReadable] = useState(() => {
    try {
      return cronstrue.toString(value)
    } catch {
      return ''
    }
  })

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const v = e.target.value
    onChange(v)
    try {
      setHumanReadable(cronstrue.toString(v))
    } catch {
      setHumanReadable('')
    }
  }

  return (
    <div className="space-y-1">
      <Label htmlFor="cron-input">Cron Expression</Label>
      <Input
        id="cron-input"
        placeholder="0 2 * * *"
        value={value}
        onChange={handleChange}
        className={error ? 'border-destructive' : ''}
      />
      {humanReadable && (
        <p className="text-xs text-muted-foreground">{humanReadable}</p>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  )
}
