import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useQuery } from '@tanstack/react-query'
import {
  CheckCircle2,
  Loader2,
  Lock,
  RotateCcw,
  XCircle,
} from 'lucide-react'
import { connectionsApi, type ConnectionType } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import {
  EngineLogo,
  getConnectorMeta,
} from '@/components/connections/EngineLogo'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ApiErrorMessage } from '@/components/shared/ApiErrorMessage'
import { cn } from '@/lib/utils'

const baseSchema = z.object({
  name: z.string().min(1, 'Name is required'),
  type: z.string().min(1, 'Select a connector type'),
  max_concurrent_jobs: z.coerce.number().int().min(1).max(64).default(2),
})

type BaseFormValues = z.infer<typeof baseSchema>

const SQL_PRESETS = [
  { id: 'postgresql', label: 'PostgreSQL', template: 'postgresql://user:password@host:5432/dbname' },
  { id: 'mysql', label: 'MySQL', template: 'mysql://user:password@host:3306/dbname' },
  { id: 'oracle', label: 'Oracle', template: 'oracle://user:password@host:1521/service_name' },
  { id: 'mssql', label: 'SQL Server', template: 'mssql://user:password@host:1433/dbname' },
] as const

interface ConnectionFormProps {
  initialValues?: {
    name?: string
    type?: string
    max_concurrent_jobs?: number
    key_version?: number
  }
  editing?: boolean
  onSubmit: (data: Record<string, unknown>) => Promise<void>
  submitLabel?: string
  isLoading?: boolean
  error?: unknown
}

export function ConnectionForm({
  initialValues,
  editing = false,
  onSubmit,
  submitLabel = 'Save Connection',
  isLoading,
  error,
}: ConnectionFormProps) {
  const [selectedType, setSelectedType] = useState<string>(String(initialValues?.type ?? 'sql'))
  const [dynamicFields, setDynamicFields] = useState<Record<string, string>>({})
  const [rotateCredentials, setRotateCredentials] = useState<boolean>(!editing)
  const [testResult, setTestResult] = useState<{ ok: boolean; latency_ms?: number; error?: string } | null>(null)
  const [testing, setTesting] = useState(false)

  const { data: types } = useQuery({
    queryKey: queryKeys.connections.types,
    queryFn: () => connectionsApi.types().then((r) => r.data),
  })

  const {
    register,
    handleSubmit,
    setValue,
    watch,
    formState: { errors },
  } = useForm<BaseFormValues>({
    resolver: zodResolver(baseSchema),
    defaultValues: {
      name: String(initialValues?.name ?? ''),
      type: String(initialValues?.type ?? 'sql'),
      max_concurrent_jobs: initialValues?.max_concurrent_jobs ?? 2,
    },
  })

  const watchedName = watch('name')
  const currentTypeDef = types?.find((t: ConnectionType) => t.type === selectedType)
  const currentMeta = getConnectorMeta(selectedType, watchedName || dynamicFields.connection)

  useEffect(() => {
    setTestResult(null)
  }, [selectedType, dynamicFields])

  function handleTypeChange(val: string) {
    setSelectedType(val)
    setValue('type', val)
    const def = types?.find((t: ConnectionType) => t.type === val)
    const defaults: Record<string, string> = {}
    for (const field of def?.fields ?? []) {
      if (field.default !== undefined && field.default !== null) {
        defaults[field.name] = String(field.default)
      }
    }
    setDynamicFields(defaults)
  }

  function handleDynamicChange(fieldName: string, value: string) {
    setDynamicFields((prev) => ({ ...prev, [fieldName]: value }))
  }

  async function handleTest() {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await connectionsApi.test({ type: selectedType, config: dynamicFields })
      setTestResult(res.data)
    } catch {
      setTestResult({ ok: false, error: 'Connection test failed' })
    } finally {
      setTesting(false)
    }
  }

  async function handleFormSubmit(base: BaseFormValues) {
    await onSubmit({
      name: base.name,
      type: base.type,
      max_concurrent_jobs: base.max_concurrent_jobs,
      config: rotateCredentials ? dynamicFields : {},
    })
  }

  const gcsAuthMode = selectedType === 'gcs_parquet' ? (dynamicFields.auth_mode || 'adc') : ''
  const sourceTypes = (types ?? []).filter((t) => getConnectorMeta(t.type).role === 'source')
  const destTypes = (types ?? []).filter((t) => getConnectorMeta(t.type).role === 'destination')

  return (
    <form onSubmit={handleSubmit(handleFormSubmit)} className="space-y-6">
      {/* Connector Picker (Create) or Locked Header (Edit) */}
      {editing ? (
        <div className="flex items-center justify-between rounded-xl border border-border/80 bg-secondary/35 px-4 py-3">
          <div className="flex items-center gap-3">
            <EngineLogo type={selectedType} name={watchedName || dynamicFields.connection} size="md" />
            <div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold">{currentMeta.engineName}</span>
                <Badge variant="secondary" className="text-[10.5px]">
                  {currentMeta.role === 'source' ? 'Source' : 'Destination'}
                </Badge>
              </div>
              <p className="text-[11px] text-muted-foreground">Connector type cannot be changed after creation.</p>
            </div>
          </div>
          <Lock className="h-3.5 w-3.5 text-muted-foreground" />
        </div>
      ) : (
        <div className="space-y-4">
          <div className="space-y-2">
            <Label className="text-[11px] uppercase tracking-wider text-muted-foreground">Sources</Label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              {sourceTypes.map((t) => (
                <ConnectorChoiceCard
                  key={t.type}
                  type={t.type}
                  selected={selectedType === t.type}
                  onSelect={() => handleTypeChange(t.type)}
                />
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <Label className="text-[11px] uppercase tracking-wider text-muted-foreground">
              Destinations (Parquet)
            </Label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              {destTypes.map((t) => (
                <ConnectorChoiceCard
                  key={t.type}
                  type={t.type}
                  selected={selectedType === t.type}
                  onSelect={() => handleTypeChange(t.type)}
                />
              ))}
            </div>
          </div>
          {errors.type && <p className="text-xs text-destructive">{errors.type.message}</p>}
        </div>
      )}

      {/* Main fields card */}
      <Card>
        <CardContent className="p-5 space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className={cn('space-y-1.5', currentMeta.role === 'source' ? 'sm:col-span-2' : 'sm:col-span-3')}>
              <Label htmlFor="conn-name">Name</Label>
              <Input
                id="conn-name"
                placeholder={
                  selectedType === 'sql'
                    ? 'pg_orders_source'
                    : selectedType === 'gcs_parquet'
                      ? 'gcs_bronze_lake'
                      : selectedType === 's3_parquet'
                        ? 'minio_bronze_dest'
                        : selectedType === 'local_parquet'
                          ? 'local_disk_dest'
                          : 'sftp_partner_source'
                }
                {...register('name')}
              />
              {errors.name && <p className="text-xs text-destructive">{errors.name.message}</p>}
            </div>

            {currentMeta.role === 'source' && (
              <div className="space-y-1.5">
                <Label htmlFor="max-concurrent">Max Concurrent Jobs</Label>
                <Input
                  id="max-concurrent"
                  type="number"
                  min={1}
                  max={64}
                  {...register('max_concurrent_jobs')}
                />
                {errors.max_concurrent_jobs && (
                  <p className="text-xs text-destructive">{errors.max_concurrent_jobs.message}</p>
                )}
              </div>
            )}
          </div>

          {/* Credentials section */}
          <div className="pt-2 border-t border-border/60 space-y-4">
            {editing && (
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">
                  Credentials · encrypted key v{initialValues?.key_version ?? 1}
                </span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setRotateCredentials((v) => !v)
                    setDynamicFields({})
                    setTestResult(null)
                  }}
                >
                  <RotateCcw className="h-3 w-3 mr-1.5" />
                  {rotateCredentials ? 'Keep Current Credentials' : 'Update Credentials'}
                </Button>
              </div>
            )}

            {rotateCredentials && (
              <>
                {selectedType === 'sql' && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-[11px] text-muted-foreground mr-1">Template:</span>
                    {SQL_PRESETS.map((preset) => (
                      <button
                        key={preset.id}
                        type="button"
                        onClick={() => handleDynamicChange('connection', preset.template)}
                        className="px-2 py-0.5 rounded-md text-[11px] border border-border/80 bg-secondary/60 hover:bg-secondary text-foreground transition-colors"
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                )}

                {currentTypeDef?.fields.map((field) => {
                  if (
                    selectedType === 'gcs_parquet' &&
                    field.name === 'credentials_json' &&
                    gcsAuthMode !== 'service_account'
                  ) {
                    return null
                  }

                  const isSecret =
                    Boolean(field.secret) ||
                    field.name.toLowerCase().includes('password') ||
                    field.name.toLowerCase().includes('secret')
                  const placeholder = field.placeholder ?? field.name
                  const value =
                    dynamicFields[field.name] ??
                    (field.default !== undefined ? String(field.default) : '')

                  return (
                    <div key={field.name} className="space-y-1.5">
                      <Label>
                        {field.label ?? field.name}
                        {field.required && <span className="text-destructive ml-1">*</span>}
                      </Label>
                      {field.type === 'select' && field.options ? (
                        <>
                          <Select value={value} onValueChange={(v) => handleDynamicChange(field.name, v)}>
                            <SelectTrigger>
                              <SelectValue placeholder={placeholder} />
                            </SelectTrigger>
                            <SelectContent>
                              {field.options.map((opt) => (
                                <SelectItem key={opt.value} value={opt.value}>
                                  {opt.label}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          {selectedType === 'gcs_parquet' &&
                            field.name === 'auth_mode' &&
                            gcsAuthMode === 'adc' && (
                              <p className="text-[11px] text-muted-foreground">
                                Uses GCP VM / GKE Workload Identity / Cloud Run credentials automatically.
                              </p>
                            )}
                        </>
                      ) : field.type === 'textarea' ? (
                        <textarea
                          rows={5}
                          className="flex w-full rounded-lg border border-input bg-background/80 px-3 py-2 text-xs font-mono ring-offset-background placeholder:text-muted-foreground/70 focus-visible:outline-none focus-visible:border-foreground/40 focus-visible:ring-2 focus-visible:ring-foreground/10"
                          placeholder={placeholder}
                          value={value}
                          onChange={(e) => handleDynamicChange(field.name, e.target.value)}
                        />
                      ) : (
                        <Input
                          type={isSecret ? 'password' : 'text'}
                          placeholder={placeholder}
                          value={value}
                          onChange={(e) => handleDynamicChange(field.name, e.target.value)}
                        />
                      )}
                    </div>
                  )
                })}

                {selectedType && currentTypeDef && (
                  <div className="flex items-center gap-3 pt-1">
                    <Button type="button" variant="outline" size="sm" onClick={handleTest} disabled={testing}>
                      {testing && <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />}
                      Test Connection
                    </Button>
                    {testResult && (
                      <span
                        className={`flex items-center gap-1.5 text-xs ${
                          testResult.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-destructive'
                        }`}
                      >
                        {testResult.ok ? (
                          <>
                            <CheckCircle2 className="h-3.5 w-3.5" />
                            Connected {testResult.latency_ms != null && `(${testResult.latency_ms}ms)`}
                          </>
                        ) : (
                          <>
                            <XCircle className="h-3.5 w-3.5" />
                            {testResult.error ?? 'Failed'}
                          </>
                        )}
                      </span>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        </CardContent>
      </Card>

      {error != null && <ApiErrorMessage error={error} />}

      <Button type="submit" disabled={isLoading}>
        {isLoading && <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />}
        {submitLabel}
      </Button>
    </form>
  )
}

function ConnectorChoiceCard({
  type,
  selected,
  onSelect,
}: {
  type: string
  selected: boolean
  onSelect: () => void
}) {
  const meta = getConnectorMeta(type)
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        'flex items-center gap-3 p-3 rounded-xl border text-left transition-all duration-150',
        selected
          ? 'border-foreground bg-card shadow-apple-sm ring-1 ring-foreground/10'
          : 'border-border/75 bg-card/60 hover:bg-card hover:border-border',
      )}
    >
      <EngineLogo type={type} size="md" />
      <div className="min-w-0 flex-1">
        <div className="text-xs font-semibold">{meta.title}</div>
        <p className="text-[11px] text-muted-foreground truncate mt-0.5">{meta.summary}</p>
      </div>
    </button>
  )
}
