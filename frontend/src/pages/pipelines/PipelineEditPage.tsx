import { lazy, Suspense, useEffect } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { pipelinesApi } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { PageHeader } from '@/components/shared/PageHeader'
import { ConnectionSelect } from '@/components/shared/ConnectionSelect'
import { ApiErrorMessage } from '@/components/shared/ApiErrorMessage'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent } from '@/components/ui/card'
import { toast } from '@/hooks/use-toast'

const QueryEditor = lazy(() => import('@/components/shared/QueryEditor'))

const schema = z.object({
  name: z.string().min(1, 'Name is required'),
  source_conn: z.string().min(1, 'Source connection is required'),
  source_query: z.string().min(1, 'Query is required'),
  dest_conn: z.string().min(1, 'Destination connection is required'),
  dest_prefix: z.string().min(1, 'Destination prefix is required'),
  extraction_mode: z.enum(['full_refresh', 'incremental']),
  incremental_key: z.string().optional(),
  min_rows_expected: z.coerce.number().int().min(0).optional().or(z.literal('')),
  max_rows_drop_pct: z.coerce.number().min(0).max(100).optional().or(z.literal('')),
  is_active: z.boolean(),
})

type FormValues = z.infer<typeof schema>

export function PipelineEditPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const { data: pipeline, isLoading, error } = useQuery({
    queryKey: queryKeys.pipelines.detail(id!),
    queryFn: () => pipelinesApi.get(id!).then((r) => r.data),
    enabled: !!id,
  })

  const { register, handleSubmit, setValue, watch, reset, formState: { errors } } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: '',
      source_conn: '',
      source_query: '',
      dest_conn: '',
      dest_prefix: '',
      extraction_mode: 'full_refresh',
      incremental_key: '',
      min_rows_expected: '',
      max_rows_drop_pct: '',
      is_active: true,
    },
  })

  useEffect(() => {
    if (!pipeline) return
    reset({
      name: pipeline.name,
      source_conn: pipeline.source_conn,
      source_query: pipeline.source_query,
      dest_conn: pipeline.dest_conn,
      dest_prefix: pipeline.dest_prefix,
      extraction_mode: pipeline.extraction_mode,
      incremental_key: pipeline.incremental_key ?? '',
      min_rows_expected: pipeline.min_rows_expected ?? '',
      max_rows_drop_pct: pipeline.max_rows_drop_pct ?? '',
      is_active: pipeline.is_active,
    })
  }, [pipeline, reset])

  const updateMutation = useMutation({
    mutationFn: (data: unknown) => pipelinesApi.update(id!, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.pipelines.detail(id!) })
      qc.invalidateQueries({ queryKey: queryKeys.pipelines.all })
      toast({ title: 'Pipeline updated' })
      navigate(`/pipelines/${id}`)
    },
  })

  function onSubmit(values: FormValues) {
    updateMutation.mutate({
      name: values.name,
      source_conn: values.source_conn,
      source_query: values.source_query,
      dest_conn: values.dest_conn,
      dest_prefix: values.dest_prefix,
      extraction_mode: values.extraction_mode,
      incremental_key: values.incremental_key || null,
      min_rows_expected: values.min_rows_expected === '' ? null : values.min_rows_expected,
      max_rows_drop_pct: values.max_rows_drop_pct === '' ? null : values.max_rows_drop_pct,
      is_active: values.is_active,
    })
  }

  if (isLoading) return <div className="flex justify-center py-12"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" /></div>
  if (error || !pipeline) return <ApiErrorMessage error={error} />

  const extractionMode = watch('extraction_mode')

  return (
    <div className="max-w-2xl">
      <PageHeader
        title={`Edit: ${pipeline.name}`}
        breadcrumb={
          <>
            <Link to="/pipelines" className="hover:underline">Pipelines</Link>
            {' / '}
            <Link to={`/pipelines/${id}`} className="hover:underline">{pipeline.name}</Link>
          </>
        }
      />

      <form onSubmit={handleSubmit(onSubmit)}>
        <Card>
          <CardContent className="pt-6 space-y-5">
            <div className="space-y-2">
              <Label htmlFor="name">Name</Label>
              <Input id="name" {...register('name')} />
              {errors.name && <p className="text-xs text-destructive">{errors.name.message}</p>}
            </div>

            <div className="space-y-2">
              <Label>Source Connection</Label>
              <ConnectionSelect
                value={watch('source_conn')}
                onChange={(v) => setValue('source_conn', v)}
                filterType="sql"
              />
              {errors.source_conn && <p className="text-xs text-destructive">{errors.source_conn.message}</p>}
            </div>

            <div className="space-y-2">
              <Label>SQL Query</Label>
              <Suspense fallback={
                <textarea
                  className="w-full min-h-[160px] rounded-md border border-input px-3 py-2 text-sm font-mono"
                  value={watch('source_query')}
                  onChange={(e) => setValue('source_query', e.target.value)}
                />
              }>
                <QueryEditor
                  value={watch('source_query')}
                  onChange={(v) => setValue('source_query', v)}
                />
              </Suspense>
              {errors.source_query && <p className="text-xs text-destructive">{errors.source_query.message}</p>}
            </div>

            <div className="space-y-2">
              <Label>Extraction Mode</Label>
              <div className="flex gap-4">
                {(['full_refresh', 'incremental'] as const).map((mode) => (
                  <label key={mode} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      value={mode}
                      checked={extractionMode === mode}
                      onChange={() => setValue('extraction_mode', mode)}
                    />
                    <span className="text-sm capitalize">{mode.replace('_', ' ')}</span>
                  </label>
                ))}
              </div>
            </div>

            {extractionMode === 'incremental' && (
              <div className="space-y-2">
                <Label htmlFor="incremental_key">Watermark Column</Label>
                <Input id="incremental_key" placeholder="updated_at" {...register('incremental_key')} />
              </div>
            )}

            <div className="space-y-2">
              <Label>Destination Connection</Label>
              <ConnectionSelect
                value={watch('dest_conn')}
                onChange={(v) => setValue('dest_conn', v)}
                filterType="s3_parquet"
              />
              {errors.dest_conn && <p className="text-xs text-destructive">{errors.dest_conn.message}</p>}
            </div>

            <div className="space-y-2">
              <Label htmlFor="dest_prefix">S3 Prefix</Label>
              <Input id="dest_prefix" placeholder="bronze/my-table/" {...register('dest_prefix')} />
              {errors.dest_prefix && <p className="text-xs text-destructive">{errors.dest_prefix.message}</p>}
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="min_rows_expected">
                  Min Rows Expected <span className="text-muted-foreground text-xs">(optional)</span>
                </Label>
                <Input id="min_rows_expected" type="number" placeholder="0" {...register('min_rows_expected')} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="max_rows_drop_pct">
                  Max Drop % <span className="text-muted-foreground text-xs">(optional)</span>
                </Label>
                <Input id="max_rows_drop_pct" type="number" min="0" max="100" placeholder="0–100" {...register('max_rows_drop_pct')} />
              </div>
            </div>

            <div className="flex items-center gap-2">
              <input
                id="is_active"
                type="checkbox"
                checked={watch('is_active')}
                onChange={(e) => setValue('is_active', e.target.checked)}
                className="h-4 w-4 rounded border"
              />
              <Label htmlFor="is_active">Active</Label>
            </div>

            {updateMutation.error && <ApiErrorMessage error={updateMutation.error} />}

            <div className="flex gap-2 pt-2">
              <Button type="button" variant="outline" onClick={() => navigate(`/pipelines/${id}`)}>
                Cancel
              </Button>
              <Button type="submit" disabled={updateMutation.isPending}>
                {updateMutation.isPending ? 'Saving…' : 'Save Changes'}
              </Button>
            </div>
          </CardContent>
        </Card>
      </form>
    </div>
  )
}
