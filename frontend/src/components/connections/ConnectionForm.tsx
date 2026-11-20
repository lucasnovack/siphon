import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useQuery } from '@tanstack/react-query'
import {
  CheckCircle2,
  KeyRound,
  Loader2,
  Lock,
  Pencil,
  RotateCcw,
  ShieldCheck,
  XCircle,
} from 'lucide-react'
import { connectionsApi, type ConnectionType } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import {
  ConnectionRoleBadge,
  EngineLogo,
  getConnectorMeta,
} from '@/components/connections/EngineLogo'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
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
  { id: 'mysql', label: 'MySQL / MariaDB', template: 'mysql://user:password@host:3306/dbname' },
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
  submitLabel = 'Create Connection',
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
  const currentMeta = getConnectorMeta(selectedType, watchedName)

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
      setTestResult({ ok: false, error: 'Connection test failed — check credentials and host' })
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
      {/* 1. Connector Type Picker (Create) OR Locked Identity Card (Edit) */}
      {editing ? (
        <Card className="bg-secondary/35 border-border/80">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center justify-between text-xs uppercase tracking-wider text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <Lock className="h-3.5 w-3.5 text-amber-500" />
                Locked Connector Identity (Immutable)
              </span>
              <ConnectionRoleBadge type={selectedType} />
            </CardTitle>
          </CardHeader>
          <CardContent className="flex items-center gap-3.5">
            <EngineLogo type={selectedType} name={watchedName} size="lg" />
            <div className="space-y-0.5">
              <div className="text-sm font-semibold">{currentMeta.engineName}</div>
              <p className="text-xs text-muted-foreground">
                Connector type (<code className="text-foreground">{selectedType}</code>) and direction cannot be changed after creation so linked pipelines remain valid.
              </p>
            </div>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                1. Sources — Read-Only Extraction
              </Label>
              <span className="text-[11px] text-muted-foreground">Extracts data into Siphon (no Reverse ETL)</span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
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
            <div className="flex items-center justify-between">
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                2. Destinations — Bronze Parquet Object Storage
              </Label>
              <span className="text-[11px] text-muted-foreground">Receives partitioned Parquet datasets</span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
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

      {/* 2. Editable General Settings */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-1.5 text-xs uppercase tracking-wider text-muted-foreground">
            <Pencil className="h-3.5 w-3.5 text-emerald-500" />
            {editing ? 'Editable Metadata & Concurrency' : 'General Settings'}
          </CardTitle>
          <CardDescription>
            {editing
              ? 'You can update these settings and save immediately without re-entering credentials.'
              : `Configure the display name for this ${currentMeta.engineName} connection.`}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className={cn('space-y-1.5', currentMeta.role === 'source' ? 'sm:col-span-2' : 'sm:col-span-3')}>
              <Label htmlFor="conn-name">
                Connection Name <span className="text-destructive">*</span>
              </Label>
              <Input
                id="conn-name"
                placeholder={
                  selectedType === 'sql'
                    ? 'prod-postgres-orders'
                    : selectedType === 'gcs_parquet'
                      ? 'gcp-bronze-lake'
                      : selectedType === 's3_parquet'
                        ? 'minio-bronze-lake'
                        : 'partner-sftp-drop'
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
        </CardContent>
      </Card>

      {/* 3. Connection Credentials (Always open on Create, Toggleable Rotation on Edit) */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between gap-2">
            <div>
              <CardTitle className="flex items-center gap-1.5 text-xs uppercase tracking-wider text-muted-foreground">
                <KeyRound className="h-3.5 w-3.5" />
                {editing ? 'Encrypted Credentials (Write-Only)' : `${currentMeta.title} Credentials`}
              </CardTitle>
              <CardDescription className="mt-1">
                {editing
                  ? `Stored encrypted with Fernet (Key v${initialValues?.key_version ?? 1}). Existing secrets are preserved unless you rotate them.`
                  : 'Encrypted with Fernet AES-128 before being stored in PostgreSQL.'}
              </CardDescription>
            </div>

            {editing && (
              <Button
                type="button"
                variant={rotateCredentials ? 'secondary' : 'outline'}
                size="sm"
                onClick={() => {
                  setRotateCredentials((v) => !v)
                  setDynamicFields({})
                  setTestResult(null)
                }}
              >
                <RotateCcw className="h-3 w-3 mr-1.5" />
                {rotateCredentials ? 'Cancel Rotation' : 'Rotate Credentials'}
              </Button>
            )}
          </div>
        </CardHeader>

        <CardContent className="space-y-4">
          {!rotateCredentials && editing ? (
            <div className="flex items-center gap-2.5 rounded-lg border border-border/70 bg-secondary/40 px-3.5 py-2.5 text-xs text-muted-foreground">
              <ShieldCheck className="h-4 w-4 text-emerald-500 shrink-0" />
              <span>
                Keeping existing encrypted credentials (<strong className="text-foreground">Key v{initialValues?.key_version ?? 1}</strong>). Click{' '}
                <strong className="text-foreground">Rotate Credentials</strong> above only if you want to replace the stored secret.
              </span>
            </div>
          ) : (
            <>
              {/* Quick SQL dialect URI template helper */}
              {selectedType === 'sql' && (
                <div className="space-y-1.5">
                  <Label className="text-[11px] text-muted-foreground">Quick Engine URI Template</Label>
                  <div className="flex flex-wrap gap-1.5">
                    {SQL_PRESETS.map((preset) => (
                      <button
                        key={preset.id}
                        type="button"
                        onClick={() => handleDynamicChange('connection', preset.template)}
                        className="px-2.5 py-1 rounded-md text-[11px] border border-border/80 bg-secondary/60 hover:bg-secondary text-foreground transition-colors"
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
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
                              Automatically integrates with the attached GCP VM Service Account, GKE Workload Identity, Cloud Run identity, or GOOGLE_APPLICATION_CREDENTIALS — no key JSON required.
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
                    Test Credentials
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
        </CardContent>
      </Card>

      {error != null && <ApiErrorMessage error={error} />}

      <div className="flex items-center gap-3">
        <Button type="submit" disabled={isLoading}>
          {isLoading && <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />}
          {submitLabel}
        </Button>
      </div>
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
        'flex items-start gap-3.5 p-3.5 rounded-xl border text-left transition-all duration-150',
        selected
          ? 'border-foreground bg-card shadow-apple-md ring-1 ring-foreground/15'
          : 'border-border/75 bg-card/60 hover:bg-card hover:border-border',
      )}
    >
      <EngineLogo type={type} size="md" />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-semibold">{meta.title}</span>
          <ConnectionRoleBadge type={type} />
        </div>
        <p className="text-[11px] text-muted-foreground leading-snug">{meta.summary}</p>
        <div className="text-[10.5px] text-muted-foreground/80 font-mono truncate pt-0.5">
          {meta.protocolHint}
        </div>
      </div>
    </button>
  )
}
