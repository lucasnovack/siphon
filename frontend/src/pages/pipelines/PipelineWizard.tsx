import { lazy, Suspense, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { pipelinesApi, previewApi, type PreviewResponse } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { ConnectionSelect } from '@/components/shared/ConnectionSelect'
import { CronInput } from '@/components/shared/CronInput'
import { ApiErrorMessage } from '@/components/shared/ApiErrorMessage'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent } from '@/components/ui/card'
import { toast } from '@/hooks/use-toast'

const QueryEditor = lazy(() => import('@/components/shared/QueryEditor'))

const step1Schema = z.object({ source_conn: z.string().min(1) })
const step2Schema = z.object({
  source_query: z.string().min(1),
  extraction_mode: z.enum(['full_refresh', 'incremental']),
  incremental_key: z.string().optional(),
})
const step3Schema = z.object({
  dest_conn: z.string().min(1),
  dest_prefix: z.string().min(1),
  min_rows_expected: z.coerce.number().optional(),
  max_rows_drop_pct: z.coerce.number().min(0).max(100).optional(),
})
const step4Schema = z.object({
  name: z.string().min(1),
  cron_expr: z.string().optional(),
})

type Step1 = z.infer<typeof step1Schema>
type Step2 = z.infer<typeof step2Schema>
type Step3 = z.infer<typeof step3Schema>
type Step4 = z.infer<typeof step4Schema>

const STEPS = ['Source', 'Query & Preview', 'Destination', 'Schedule & Review']

export function PipelineWizard() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [step, setStep] = useState(0)
  const [step1, setStep1] = useState<Step1>({ source_conn: '' })
  const [step2, setStep2] = useState<Step2>({ source_query: '', extraction_mode: 'full_refresh' })
  const [step3, setStep3] = useState<Step3>({ dest_conn: '', dest_prefix: '' })
  const [preview, setPreview] = useState<PreviewResponse | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [createError, setCreateError] = useState<unknown>(null)

  const form4 = useForm<Step4>({ resolver: zodResolver(step4Schema), defaultValues: { name: '', cron_expr: '' } })

  const createMutation = useMutation({
    mutationFn: (data: unknown) => pipelinesApi.create(data),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: queryKeys.pipelines.all })
      toast({ title: 'Pipeline created' })
      navigate(`/pipelines/${res.data.id}`)
    },
    onError: (err) => setCreateError(err),
  })

  async function handlePreview() {
    setPreviewing(true)
    setPreview(null)
    try {
      const res = await previewApi.run(step1.source_conn, step2.source_query)
      setPreview(res.data)
    } catch {
      toast({ title: 'Preview failed', variant: 'destructive' })
    } finally {
      setPreviewing(false)
    }
  }

  function handleCreate(s4: Step4) {
    createMutation.mutate({
      name: s4.name,
      source_conn: step1.source_conn,
      source_query: step2.source_query,
      extraction_mode: step2.extraction_mode,
      incremental_key: step2.incremental_key ?? null,
      dest_conn: step3.dest_conn,
      dest_prefix: step3.dest_prefix,
      min_rows_expected: step3.min_rows_expected ?? null,
      max_rows_drop_pct: step3.max_rows_drop_pct ?? null,
      schedule: s4.cron_expr ? { cron_expr: s4.cron_expr, is_active: true } : null,
    })
  }

  return (
    <div className="max-w-2xl">
      <PageHeader title="New Pipeline" breadcrumb={<span>Pipelines / New</span>} />

      {/* Step indicators */}
      <div className="flex items-center mb-8">
        {STEPS.map((label, i) => (
          <div key={i} className="flex items-center">
            <div className={`flex items-center justify-center w-8 h-8 rounded-full text-sm font-medium ${
              i < step ? 'bg-primary text-primary-foreground' :
              i === step ? 'bg-primary text-primary-foreground ring-2 ring-primary ring-offset-2' :
              'bg-muted text-muted-foreground'
            }`}>
              {i + 1}
            </div>
            <span className={`ml-2 text-sm hidden sm:inline ${i === step ? 'font-medium' : 'text-muted-foreground'}`}>
              {label}
            </span>
            {i < STEPS.length - 1 && <div className="mx-4 h-px w-8 bg-border" />}
          </div>
        ))}
      </div>

      <Card>
        <CardContent className="pt-6 space-y-4">
          {/* Step 0: Source */}
          {step === 0 && (
            <>
              <div className="space-y-2">
                <Label>Source Connection</Label>
                <ConnectionSelect
                  value={step1.source_conn}
                  onChange={(v) => setStep1({ source_conn: v })}
                  filterType="sql"
                />
              </div>
              <Button
                disabled={!step1.source_conn}
                onClick={() => setStep(1)}
                className="w-full"
              >
                Next: Query
              </Button>
            </>
          )}

          {/* Step 1: Query + Preview */}
          {step === 1 && (
            <>
              <div className="space-y-2">
                <Label>SQL Query</Label>
                <Suspense fallback={<textarea
                  className="w-full min-h-[160px] rounded-md border border-input px-3 py-2 text-sm font-mono"
                  value={step2.source_query}
                  onChange={(e) => setStep2((s) => ({ ...s, source_query: e.target.value }))}
                  placeholder="SELECT * FROM table"
                />}>
                  <QueryEditor
                    value={step2.source_query}
                    onChange={(v) => setStep2((s) => ({ ...s, source_query: v }))}
                  />
                </Suspense>
              </div>

              <div className="space-y-2">
                <Label>Extraction Mode</Label>
                <div className="flex gap-3">
                  {(['full_refresh', 'incremental'] as const).map((mode) => (
                    <label key={mode} className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        value={mode}
                        checked={step2.extraction_mode === mode}
                        onChange={() => setStep2((s) => ({ ...s, extraction_mode: mode }))}
                      />
                      <span className="text-sm capitalize">{mode.replace('_', ' ')}</span>
                    </label>
                  ))}
                </div>
              </div>

              {step2.extraction_mode === 'incremental' && (
                <div className="space-y-2">
                  <Label>Watermark Column</Label>
                  <Input
                    placeholder="updated_at"
                    value={step2.incremental_key ?? ''}
                    onChange={(e) => setStep2((s) => ({ ...s, incremental_key: e.target.value }))}
                  />
                </div>
              )}

              <div className="flex gap-2">
                <Button variant="outline" onClick={() => setStep(0)}>Back</Button>
                <Button variant="outline" onClick={handlePreview} disabled={!step2.source_query || previewing}>
                  {previewing ? 'Loading…' : 'Preview (100 rows)'}
                </Button>
                <Button disabled={!step2.source_query} onClick={() => setStep(2)}>Next</Button>
              </div>

              {preview && (
                <div className="overflow-auto max-h-64 rounded-md border text-xs">
                  <table className="w-full">
                    <thead className="bg-muted sticky top-0">
                      <tr>{preview.columns.map((c) => <th key={c} className="px-2 py-1 text-left font-medium">{c}</th>)}</tr>
                    </thead>
                    <tbody>
                      {preview.rows.map((row, i) => (
                        <tr key={i} className="border-t">
                          {(row as unknown[]).map((cell, j) => (
                            <td key={j} className="px-2 py-1 max-w-xs truncate">{String(cell ?? '')}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="px-2 py-1 text-muted-foreground border-t">{preview.row_count} rows</div>
                </div>
              )}
            </>
          )}

          {/* Step 2: Destination */}
          {step === 2 && (
            <>
              <div className="space-y-2">
                <Label>Destination Connection</Label>
                <ConnectionSelect
                  value={step3.dest_conn}
                  onChange={(v) => setStep3((s) => ({ ...s, dest_conn: v }))}
                  filterType="s3_parquet"
                />
              </div>
              <div className="space-y-2">
                <Label>S3 Prefix</Label>
                <Input
                  placeholder="bronze/my-table/"
                  value={step3.dest_prefix}
                  onChange={(e) => setStep3((s) => ({ ...s, dest_prefix: e.target.value }))}
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>Min Rows Expected <span className="text-muted-foreground text-xs">(optional)</span></Label>
                  <Input
                    type="number"
                    placeholder="0"
                    value={step3.min_rows_expected ?? ''}
                    onChange={(e) => setStep3((s) => ({ ...s, min_rows_expected: e.target.value ? Number(e.target.value) : undefined }))}
                  />
                </div>
                <div className="space-y-2">
                  <Label>Max Drop % <span className="text-muted-foreground text-xs">(optional)</span></Label>
                  <Input
                    type="number"
                    placeholder="0–100"
                    min="0"
                    max="100"
                    value={step3.max_rows_drop_pct ?? ''}
                    onChange={(e) => setStep3((s) => ({ ...s, max_rows_drop_pct: e.target.value ? Number(e.target.value) : undefined }))}
                  />
                </div>
              </div>
              <div className="flex gap-2">
                <Button variant="outline" onClick={() => setStep(1)}>Back</Button>
                <Button disabled={!step3.dest_conn || !step3.dest_prefix} onClick={() => setStep(3)}>Next</Button>
              </div>
            </>
          )}

          {/* Step 3: Schedule + Review */}
          {step === 3 && (
            <form onSubmit={form4.handleSubmit(handleCreate)} className="space-y-4">
              <div className="space-y-2">
                <Label>Pipeline Name</Label>
                <Input placeholder="my-pipeline" {...form4.register('name')} />
                {form4.formState.errors.name && (
                  <p className="text-xs text-destructive">{form4.formState.errors.name.message}</p>
                )}
              </div>

              <div className="space-y-2">
                <Label>Schedule <span className="text-muted-foreground text-xs">(optional)</span></Label>
                <CronInput
                  value={form4.watch('cron_expr') ?? ''}
                  onChange={(v) => form4.setValue('cron_expr', v)}
                />
              </div>

              {/* Review summary */}
              <div className="rounded-md bg-muted p-4 space-y-2 text-sm">
                <div className="font-medium mb-2">Review</div>
                <div className="grid grid-cols-2 gap-1 text-xs">
                  <span className="text-muted-foreground">Source</span><span className="font-mono">{step1.source_conn.slice(0, 8)}…</span>
                  <span className="text-muted-foreground">Mode</span><span>{step2.extraction_mode}</span>
                  <span className="text-muted-foreground">Destination</span><span className="font-mono">{step3.dest_conn.slice(0, 8)}…</span>
                  <span className="text-muted-foreground">Prefix</span><span className="font-mono">{step3.dest_prefix}</span>
                </div>
              </div>

              {createError != null && <ApiErrorMessage error={createError} />}

              <div className="flex gap-2">
                <Button type="button" variant="outline" onClick={() => setStep(2)}>Back</Button>
                <Button type="submit" disabled={createMutation.isPending}>
                  {createMutation.isPending ? 'Creating…' : 'Create Pipeline'}
                </Button>
              </div>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
