import { useEffect, useRef, useState } from 'react'
import { runsApi, type JobRun } from '@/lib/api'

interface LogViewerProps {
  runId: string
  status: JobRun['status']
}

const TERMINAL = new Set(['success', 'failed', 'partial_success'])
const MAX_LINES = 2000

export function LogViewer({ runId, status }: LogViewerProps) {
  const [lines, setLines] = useState<string[]>([])
  const [done, setDone] = useState(false)
  const offsetRef = useRef(0)
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
      // One final poll then stop
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

  // When status becomes terminal, do final poll
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

  // Auto-scroll to bottom
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [lines])

  return (
    <div className="rounded-md bg-zinc-950 text-zinc-100 p-4 font-mono text-xs overflow-auto max-h-96">
      {lines.length === 0 ? (
        <span className="text-zinc-500">Waiting for logs…</span>
      ) : (
        lines.map((line, i) => (
          <div key={i} className="whitespace-pre-wrap leading-relaxed">
            {line}
          </div>
        ))
      )}
      <div ref={endRef} />
      {done && <div className="text-zinc-500 mt-2">— end of logs —</div>}
    </div>
  )
}
