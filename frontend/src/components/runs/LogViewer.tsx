import { useEffect, useRef, useState, useMemo } from 'react'
import {
  ArrowDown,
  Check,
  Copy,
  Download,
  Filter,
  RefreshCw,
  Search,
  Terminal,
  X,
} from 'lucide-react'
import { runsApi, type JobRun } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { toast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'

interface LogViewerProps {
  runId: string
  status: JobRun['status']
  className?: string
}

const TERMINAL = new Set(['success', 'failed', 'partial_success'])
const MAX_LINES = 5000

type LogLevel = 'all' | 'error' | 'warn' | 'info'

interface ParsedLine {
  id: number
  raw: string
  timestamp?: string
  level: 'error' | 'warn' | 'info' | 'debug' | 'default'
  event?: string
  extra?: Record<string, unknown>
}

function parseLogLine(raw: string, id: number): ParsedLine {
  const trimmed = raw.trim()
  if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
    try {
      const parsed = JSON.parse(trimmed)
      const levelStr = String(parsed.level || parsed.levelname || '').toLowerCase()
      let level: ParsedLine['level'] = 'default'
      if (levelStr.includes('err') || levelStr.includes('crit') || levelStr.includes('fatal')) {
        level = 'error'
      } else if (levelStr.includes('warn')) {
        level = 'warn'
      } else if (levelStr.includes('info')) {
        level = 'info'
      } else if (levelStr.includes('debug')) {
        level = 'debug'
      }

      const timestamp = parsed.timestamp || parsed.time || parsed.asctime
      const event = parsed.event || parsed.message || parsed.msg

      // Pick other keys for structured key-values
      const extra: Record<string, unknown> = {}
      for (const [k, v] of Object.entries(parsed)) {
        if (!['level', 'levelname', 'timestamp', 'time', 'asctime', 'event', 'message', 'msg'].includes(k)) {
          extra[k] = v
        }
      }

      return { id, raw, timestamp: String(timestamp || ''), level, event: String(event || ''), extra }
    } catch {
      // fallback to plain text parsing
    }
  }

  // Plain text detection
  const lower = raw.toLowerCase()
  let level: ParsedLine['level'] = 'default'
  if (lower.includes('error') || lower.includes('exception') || lower.includes('traceback') || lower.includes('failed')) {
    level = 'error'
  } else if (lower.includes('warn')) {
    level = 'warn'
  } else if (lower.includes('info')) {
    level = 'info'
  } else if (lower.includes('debug')) {
    level = 'debug'
  }

  // Try extracting timestamp prefix like "2026-11-20 21:00:00"
  const dateMatch = raw.match(/^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)/)
  const timestamp = dateMatch ? dateMatch[1] : undefined
  const event = dateMatch ? raw.slice(dateMatch[0].length).trim() : raw

  return { id, raw, timestamp, level, event }
}

export function LogViewer({ runId, status, className }: LogViewerProps) {
  const [lines, setLines] = useState<string[]>([])
  const [done, setDone] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [levelFilter, setLevelFilter] = useState<LogLevel>('all')
  const [autoScroll, setAutoScroll] = useState(true)
  const [copied, setCopied] = useState(false)

  const offsetRef = useRef(0)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  async function fetchLogs(finalPoll = false) {
    try {
      const res = await runsApi.logs(runId, offsetRef.current)
      const { logs, next_offset } = res.data
      if (logs.length > 0) {
        setLines((prev) => {
          const combined = [...prev, ...logs]
          return combined.length > MAX_LINES ? combined.slice(-MAX_LINES) : combined
        })
        offsetRef.current = next_offset
      }
      if (finalPoll) setDone(true)
    } catch {
      // ignore log fetch errors
    }
  }

  useEffect(() => {
    fetchLogs()

    if (TERMINAL.has(status)) {
      fetchLogs(true)
      return
    }

    intervalRef.current = setInterval(async () => {
      await fetchLogs()
    }, 2000)

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])

  useEffect(() => {
    if (TERMINAL.has(status) && !done) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
      fetchLogs(true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status])

  // Auto-scroll when lines update if autoScroll is enabled
  useEffect(() => {
    if (autoScroll) {
      endRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [lines, autoScroll])

  // Parse lines
  const parsedLines = useMemo(() => {
    return lines.map((line, idx) => parseLogLine(line, idx))
  }, [lines])

  // Counts by level
  const counts = useMemo(() => {
    let error = 0
    let warn = 0
    let info = 0
    for (const l of parsedLines) {
      if (l.level === 'error') error++
      else if (l.level === 'warn') warn++
      else if (l.level === 'info') info++
    }
    return { all: parsedLines.length, error, warn, info }
  }, [parsedLines])

  // Filtered lines
  const filteredLines = useMemo(() => {
    return parsedLines.filter((l) => {
      if (levelFilter === 'error' && l.level !== 'error') return false
      if (levelFilter === 'warn' && l.level !== 'warn' && l.level !== 'error') return false
      if (levelFilter === 'info' && l.level !== 'info') return false

      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase().trim()
        return l.raw.toLowerCase().includes(q)
      }
      return true
    })
  }, [parsedLines, levelFilter, searchQuery])

  function handleCopyAll() {
    navigator.clipboard.writeText(lines.join('\n')).then(() => {
      setCopied(true)
      toast({ title: 'Logs copied to clipboard' })
      setTimeout(() => setCopied(false), 2000)
    })
  }

  function handleDownload() {
    const blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `siphon-run-${runId.slice(0, 8)}.log`
    a.click()
    URL.revokeObjectURL(url)
    toast({ title: 'Downloading log file' })
  }

  const isStreaming = !TERMINAL.has(status) && !done

  return (
    <div className={cn('rounded-xl border border-border/80 bg-zinc-950 shadow-apple-sm flex flex-col overflow-hidden', className)}>
      {/* Console Top Toolbar */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-2.5 px-4 py-2.5 bg-zinc-900/90 border-b border-zinc-800/80">
        <div className="flex items-center gap-2">
          {/* macOS styled window dots */}
          <div className="flex items-center gap-1.5 mr-2">
            <div className="h-2.5 w-2.5 rounded-full bg-red-500/80" />
            <div className="h-2.5 w-2.5 rounded-full bg-amber-500/80" />
            <div className="h-2.5 w-2.5 rounded-full bg-emerald-500/80" />
          </div>

          <div className="flex items-center gap-1.5 text-xs font-mono text-zinc-300">
            <Terminal className="h-3.5 w-3.5 text-zinc-400" />
            <span className="font-semibold">Terminal Output</span>
            {isStreaming && (
              <span className="flex items-center gap-1 text-[11px] text-emerald-400 font-mono ml-2">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
                </span>
                LIVE STREAM
              </span>
            )}
          </div>
        </div>

        {/* Toolbar Controls */}
        <div className="flex items-center gap-1.5 flex-wrap">
          {/* Search box inside console */}
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3 w-3 text-zinc-400" />
            <Input
              type="text"
              placeholder="Filter logs..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="h-7 w-36 sm:w-44 pl-7 pr-6 text-xs bg-zinc-950 border-zinc-800 text-zinc-200 placeholder:text-zinc-500 focus-visible:ring-1 focus-visible:ring-zinc-600"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-100"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </div>

          {/* Level Filter Buttons */}
          <div className="flex items-center gap-1 bg-zinc-950 p-0.5 rounded-md border border-zinc-800">
            <button
              onClick={() => setLevelFilter('all')}
              className={cn(
                'px-2 py-0.5 text-[11px] font-mono rounded transition-colors',
                levelFilter === 'all' ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-400 hover:text-zinc-200'
              )}
            >
              All ({counts.all})
            </button>
            {counts.error > 0 && (
              <button
                onClick={() => setLevelFilter('error')}
                className={cn(
                  'px-2 py-0.5 text-[11px] font-mono rounded transition-colors text-red-400',
                  levelFilter === 'error' ? 'bg-red-950/80 text-red-300' : 'hover:bg-zinc-900'
                )}
              >
                Errors ({counts.error})
              </button>
            )}
            {counts.warn > 0 && (
              <button
                onClick={() => setLevelFilter('warn')}
                className={cn(
                  'px-2 py-0.5 text-[11px] font-mono rounded transition-colors text-amber-400',
                  levelFilter === 'warn' ? 'bg-amber-950/80 text-amber-300' : 'hover:bg-zinc-900'
                )}
              >
                Warnings ({counts.warn})
              </button>
            )}
          </div>

          {/* Auto-scroll button */}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setAutoScroll(!autoScroll)}
            className={cn(
              'h-7 px-2 text-xs font-mono',
              autoScroll ? 'text-emerald-400 bg-zinc-800/60' : 'text-zinc-400 hover:text-zinc-200'
            )}
            title="Auto-scroll to bottom"
          >
            <ArrowDown className="h-3 w-3 mr-1" />
            Scroll
          </Button>

          {/* Copy button */}
          <Button
            variant="ghost"
            size="sm"
            onClick={handleCopyAll}
            disabled={lines.length === 0}
            className="h-7 px-2 text-xs font-mono text-zinc-300 hover:text-zinc-100 hover:bg-zinc-800"
            title="Copy all logs"
          >
            {copied ? <Check className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
          </Button>

          {/* Download button */}
          <Button
            variant="ghost"
            size="sm"
            onClick={handleDownload}
            disabled={lines.length === 0}
            className="h-7 px-2 text-xs font-mono text-zinc-300 hover:text-zinc-100 hover:bg-zinc-800"
            title="Download log file"
          >
            <Download className="h-3 w-3" />
          </Button>
        </div>
      </div>

      {/* Terminal Body */}
      <div
        ref={scrollContainerRef}
        className="p-4 font-mono text-[13px] leading-relaxed text-zinc-200 overflow-y-auto max-h-[580px] min-h-[300px] select-text"
      >
        {lines.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-zinc-500 space-y-2">
            <RefreshCw className="h-5 w-5 animate-spin opacity-50" />
            <span className="text-xs">Waiting for worker output…</span>
          </div>
        ) : filteredLines.length === 0 ? (
          <div className="flex items-center justify-center py-12 text-zinc-500 text-xs gap-2">
            <Filter className="h-4 w-4 opacity-50" />
            No log lines match the filter "{searchQuery}".
          </div>
        ) : (
          filteredLines.map((line) => {
            const isError = line.level === 'error'
            const isWarn = line.level === 'warn'
            const isInfo = line.level === 'info'

            return (
              <div
                key={line.id}
                className={cn(
                  'flex items-start gap-2 py-0.5 px-1.5 rounded transition-colors group',
                  isError && 'bg-red-950/30 text-red-300',
                  isWarn && 'bg-amber-950/20 text-amber-300',
                  !isError && !isWarn && 'hover:bg-zinc-900/60'
                )}
              >
                {/* Line number */}
                <span className="text-[11px] text-zinc-600 select-none w-7 shrink-0 text-right font-mono">
                  {line.id + 1}
                </span>

                {/* Timestamp */}
                {line.timestamp && (
                  <span className="text-[11px] text-zinc-500 select-none shrink-0 font-mono">
                    {line.timestamp}
                  </span>
                )}

                {/* Level badge */}
                {line.level !== 'default' && (
                  <Badge
                    variant="outline"
                    className={cn(
                      'text-[9px] uppercase px-1 py-0 select-none font-mono tracking-wider shrink-0 border-0',
                      isError && 'bg-red-500/20 text-red-400',
                      isWarn && 'bg-amber-500/20 text-amber-400',
                      isInfo && 'bg-blue-500/20 text-blue-400',
                      line.level === 'debug' && 'bg-zinc-800 text-zinc-400'
                    )}
                  >
                    {line.level}
                  </Badge>
                )}

                {/* Event or raw content */}
                <span className="whitespace-pre-wrap break-all flex-1">
                  {line.event || line.raw}
                  {line.extra && Object.keys(line.extra).length > 0 && (
                    <span className="text-zinc-500 ml-2">
                      {Object.entries(line.extra)
                        .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`)
                        .join(' ')}
                    </span>
                  )}
                </span>
              </div>
            )
          })
        )}
        <div ref={endRef} />
      </div>

      {/* Terminal Footer */}
      <div className="flex items-center justify-between px-4 py-2 bg-zinc-900/70 border-t border-zinc-800/80 text-[11px] font-mono text-zinc-500">
        <div>
          {done ? '— End of log stream —' : 'Polling job output from worker buffer…'}
        </div>
        <div>
          Showing {filteredLines.length} of {lines.length} lines
        </div>
      </div>
    </div>
  )
}
