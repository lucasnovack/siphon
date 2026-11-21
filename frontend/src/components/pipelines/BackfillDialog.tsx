import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import {
  AlertCircle,
  Calendar,
  History,
  Info,
  Play,
  RefreshCw,
} from 'lucide-react'
import { pipelinesApi, type Pipeline } from '@/lib/api'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { toast } from '@/hooks/use-toast'

interface BackfillDialogProps {
  pipeline: Pipeline
  trigger?: React.ReactNode
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export function BackfillDialog({
  pipeline,
  trigger,
  open: controlledOpen,
  onOpenChange: setControlledOpen,
}: BackfillDialogProps) {
  const navigate = useNavigate()
  const [internalOpen, setInternalOpen] = useState(false)
  const isControlled = controlledOpen !== undefined
  const open = isControlled ? controlledOpen : internalOpen
  const setOpen = isControlled ? setControlledOpen! : setInternalOpen

  // Default dates: last 7 days
  const [dateFrom, setDateFrom] = useState(() => {
    const d = new Date()
    d.setDate(d.getDate() - 7)
    d.setHours(0, 0, 0, 0)
    return d.toISOString().slice(0, 16)
  })

  const [dateTo, setDateTo] = useState(() => {
    const d = new Date()
    d.setHours(23, 59, 59, 0)
    return d.toISOString().slice(0, 16)
  })

  const isIncremental =
    pipeline.extraction_mode === 'incremental' && Boolean(pipeline.incremental_key)

  const backfillMutation = useMutation({
    mutationFn: async () => {
      // Convert to ISO 8601 strings
      const isoFrom = new Date(dateFrom).toISOString()
      const isoTo = new Date(dateTo).toISOString()
      return pipelinesApi.trigger(pipeline.id, {
        date_from: isoFrom,
        date_to: isoTo,
      })
    },
    onSuccess: (res) => {
      setOpen(false)
      toast({
        title: 'Backfill triggered',
        description: `Backfill run #${res.data.job_id.slice(0, 8)}… started`,
      })
      navigate(`/runs/${res.data.job_id}`)
    },
    onError: (err: unknown) => {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Failed to trigger backfill'
      toast({
        title: 'Backfill failed',
        description: message,
        variant: 'destructive',
      })
    },
  })

  function applyPreset(days: number) {
    const end = new Date()
    const start = new Date()
    start.setDate(end.getDate() - days)
    start.setHours(0, 0, 0, 0)
    setDateFrom(start.toISOString().slice(0, 16))
    setDateTo(end.toISOString().slice(0, 16))
  }

  function applyYesterday() {
    const start = new Date()
    start.setDate(start.getDate() - 1)
    start.setHours(0, 0, 0, 0)
    const end = new Date(start)
    end.setHours(23, 59, 59, 999)
    setDateFrom(start.toISOString().slice(0, 16))
    setDateTo(end.toISOString().slice(0, 16))
  }

  function applyLastMonth() {
    const now = new Date()
    const start = new Date(now.getFullYear(), now.getMonth() - 1, 1, 0, 0, 0)
    const end = new Date(now.getFullYear(), now.getMonth(), 0, 23, 59, 59)
    setDateFrom(start.toISOString().slice(0, 16))
    setDateTo(end.toISOString().slice(0, 16))
  }

  const isValidRange = Boolean(dateFrom && dateTo && new Date(dateFrom) < new Date(dateTo))

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      {trigger ? (
        <DialogTrigger asChild>{trigger}</DialogTrigger>
      ) : (
        <DialogTrigger asChild>
          <Button variant="outline" size="sm" className="shadow-apple-sm font-medium">
            <History className="h-3.5 w-3.5 mr-1.5" />
            Backfill
          </Button>
        </DialogTrigger>
      )}

      <DialogContent className="max-w-lg border border-border/80 shadow-apple-lg">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary shrink-0">
              <History className="h-4 w-4" />
            </div>
            <div>
              <DialogTitle className="text-lg font-bold">Historical Backfill</DialogTitle>
              <DialogDescription className="text-xs text-muted-foreground mt-0.5">
                Rerun data extraction for a specific time window without mutating the production watermark
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        {!isIncremental ? (
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 dark:bg-amber-950/20 p-4 text-xs space-y-2">
            <div className="flex items-center gap-2 font-semibold text-amber-900 dark:text-amber-200">
              <AlertCircle className="h-4 w-4 text-amber-500 shrink-0" />
              Incremental Key Required
            </div>
            <p className="text-muted-foreground leading-relaxed">
              Backfills require an <strong>incremental watermark key</strong> (such as{' '}
              <code className="font-mono text-[11px] bg-secondary px-1 py-0.5 rounded">updated_at</code> or{' '}
              <code className="font-mono text-[11px] bg-secondary px-1 py-0.5 rounded">created_at</code>)
              to inject a two-sided filter window. This pipeline is currently configured as{' '}
              <Badge variant="secondary" className="font-mono text-[10px]">
                {pipeline.extraction_mode}
              </Badge>
              .
            </p>
          </div>
        ) : (
          <div className="space-y-4 py-1">
            {/* Pipeline Context Bar */}
            <div className="flex items-center justify-between px-3 py-2 rounded-lg bg-secondary/50 border border-border/60 text-xs">
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">Watermark Column:</span>
                <span className="font-mono font-semibold text-foreground">
                  {pipeline.incremental_key}
                </span>
              </div>
              <Badge variant="outline" className="text-[10px] font-mono">
                Isolated Window
              </Badge>
            </div>

            {/* Quick Presets */}
            <div className="space-y-1.5">
              <Label className="text-xs font-medium text-muted-foreground">Quick Presets</Label>
              <div className="flex items-center gap-1.5 flex-wrap">
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={applyYesterday}
                  className="h-7 text-xs px-2.5 font-normal"
                >
                  Yesterday
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => applyPreset(7)}
                  className="h-7 text-xs px-2.5 font-normal"
                >
                  Last 7 Days
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => applyPreset(30)}
                  className="h-7 text-xs px-2.5 font-normal"
                >
                  Last 30 Days
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={applyLastMonth}
                  className="h-7 text-xs px-2.5 font-normal"
                >
                  Last Month
                </Button>
              </div>
            </div>

            {/* Date Inputs */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="date-from" className="text-xs font-semibold flex items-center gap-1.5">
                  <Calendar className="h-3.5 w-3.5 text-muted-foreground" />
                  Date From (Start)
                </Label>
                <Input
                  id="date-from"
                  type="datetime-local"
                  value={dateFrom}
                  onChange={(e) => setDateFrom(e.target.value)}
                  className="h-9 font-mono text-xs"
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="date-to" className="text-xs font-semibold flex items-center gap-1.5">
                  <Calendar className="h-3.5 w-3.5 text-muted-foreground" />
                  Date To (End)
                </Label>
                <Input
                  id="date-to"
                  type="datetime-local"
                  value={dateTo}
                  onChange={(e) => setDateTo(e.target.value)}
                  className="h-9 font-mono text-xs"
                />
              </div>
            </div>

            {/* Generated SQL Filter Preview */}
            <div className="rounded-lg bg-zinc-950 p-3 text-xs font-mono border border-zinc-800 text-zinc-300 space-y-1">
              <div className="text-[10px] text-zinc-500 uppercase tracking-wider font-sans font-semibold">
                Injected Backfill Window
              </div>
              <div className="text-emerald-400 whitespace-pre-wrap break-all">
                {pipeline.incremental_key} &gt;= '{dateFrom ? new Date(dateFrom).toISOString() : '?'}' AND{' '}
                {pipeline.incremental_key} &lt;= '{dateTo ? new Date(dateTo).toISOString() : '?'}'
              </div>
            </div>

            {/* Watermark Safety Guarantee Notice */}
            <div className="flex items-start gap-2 text-xs text-muted-foreground bg-primary/[0.03] p-2.5 rounded border border-border/50">
              <Info className="h-4 w-4 text-blue-500 shrink-0 mt-0.5" />
              <span>
                <strong>Watermark Safe:</strong> Siphon marks this run as{' '}
                <code className="text-foreground font-mono">is_backfill=True</code> so it will write
                to the lakehouse without overriding the current watermark (
                <code className="text-foreground font-mono">{pipeline.last_watermark ?? 'initial'}</code>).
              </span>
            </div>
          </div>
        )}

        <DialogFooter className="gap-2 sm:gap-0">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setOpen(false)}
            className="text-xs"
          >
            Cancel
          </Button>

          {isIncremental && (
            <Button
              type="button"
              size="sm"
              disabled={!isValidRange || backfillMutation.isPending}
              onClick={() => backfillMutation.mutate()}
              className="text-xs shadow-apple-sm font-medium"
            >
              {backfillMutation.isPending ? (
                <>
                  <RefreshCw className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                  Starting Backfill…
                </>
              ) : (
                <>
                  <Play className="h-3.5 w-3.5 mr-1.5" />
                  Execute Backfill
                </>
              )}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
