import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useQuery } from '@tanstack/react-query'
import { CheckCircle, Loader2, XCircle } from 'lucide-react'
import { connectionsApi, type ConnectionType } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ApiErrorMessage } from '@/components/shared/ApiErrorMessage'

const baseSchema = z.object({
  name: z.string().min(1, 'Name is required'),
  type: z.string().min(1, 'Type is required'),
})

interface ConnectionFormProps {
  initialValues?: Record<string, unknown>
  editing?: boolean
  onSubmit: (data: Record<string, unknown>) => Promise<void>
  submitLabel?: string
  isLoading?: boolean
  error?: unknown
}

export function ConnectionForm({ initialValues, editing = false, onSubmit, submitLabel = 'Save', isLoading, error }: ConnectionFormProps) {
  const [selectedType, setSelectedType] = useState<string>(String(initialValues?.type ?? ''))
  const [dynamicFields, setDynamicFields] = useState<Record<string, string>>({})
  const [testResult, setTestResult] = useState<{ ok: boolean; latency_ms?: number; error?: string } | null>(null)
  const [testing, setTesting] = useState(false)

  const { data: types } = useQuery({
    queryKey: queryKeys.connections.types,
    queryFn: () => connectionsApi.types().then((r) => r.data),
  })

  const { register, handleSubmit, setValue, formState: { errors } } = useForm({
    resolver: zodResolver(baseSchema),
    defaultValues: {
      name: String(initialValues?.name ?? ''),
      type: String(initialValues?.type ?? ''),
    },
  })

  const currentTypeDef = types?.find((t: ConnectionType) => t.type === selectedType)

  // Reset test result when any field changes
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
      setTestResult({ ok: false, error: 'Test failed' })
    } finally {
      setTesting(false)
    }
  }

  async function handleFormSubmit(base: { name: string; type: string }) {
    await onSubmit({ ...base, config: dynamicFields })
  }

  const gcsAuthMode = selectedType === 'gcs_parquet' ? (dynamicFields.auth_mode || 'adc') : ''

  return (
    <form onSubmit={handleSubmit(handleFormSubmit)} className="space-y-4">
      <div className="space-y-2">
        <Label>Name</Label>
        <Input placeholder="my-postgres-prod" {...register('name')} />
        {errors.name && <p className="text-xs text-destructive">{errors.name.message}</p>}
      </div>

      <div className="space-y-2">
        <Label>Type</Label>
        {editing ? (
          <div className="flex h-10 w-full items-center rounded-md border border-input bg-muted px-3 text-sm text-muted-foreground">
            {currentTypeDef?.label ?? selectedType}
          </div>
        ) : (
          <Select value={selectedType} onValueChange={handleTypeChange}>
            <SelectTrigger className={errors.type ? 'border-destructive' : ''}>
              <SelectValue placeholder="Select type" />
            </SelectTrigger>
            <SelectContent>
              {(types ?? []).map((t: ConnectionType) => (
                <SelectItem key={t.type} value={t.type}>
                  {t.label ?? t.type}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {errors.type && <p className="text-xs text-destructive">{errors.type.message}</p>}
      </div>

      {currentTypeDef?.fields.map((field) => {
        if (selectedType === 'gcs_parquet' && field.name === 'credentials_json' && gcsAuthMode !== 'service_account') {
          return null
        }

        const isSecret = Boolean(field.secret) || field.name.toLowerCase().includes('password') || field.name.toLowerCase().includes('secret')
        const placeholder = editing
          ? (isSecret ? '••••••••  (leave blank to keep current)' : '(leave blank to keep current)')
          : (field.placeholder ?? field.name)
        const value = dynamicFields[field.name] ?? (field.default !== undefined ? String(field.default) : '')

        return (
          <div key={field.name} className="space-y-2">
            <Label>
              {field.label ?? field.name}
              {field.required && !editing && <span className="text-destructive ml-1">*</span>}
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
                {selectedType === 'gcs_parquet' && field.name === 'auth_mode' && gcsAuthMode === 'adc' && (
                  <p className="text-xs text-muted-foreground">
                    Automatically uses the attached GCP VM Service Account, GKE Workload Identity, Cloud Run identity, or GOOGLE_APPLICATION_CREDENTIALS — no key JSON required.
                  </p>
                )}
              </>
            ) : field.type === 'textarea' ? (
              <textarea
                rows={5}
                className="flex w-full rounded-md border border-input bg-background px-3 py-2 text-xs font-mono ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
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
        <div className="flex items-center gap-3">
          <Button type="button" variant="outline" onClick={handleTest} disabled={testing}>
            {testing && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            Test Connection
          </Button>
          {testResult && (
            <span className={`flex items-center gap-1 text-sm ${testResult.ok ? 'text-green-600' : 'text-destructive'}`}>
              {testResult.ok ? (
                <>
                  <CheckCircle className="h-4 w-4" />
                  OK {testResult.latency_ms != null && `(${testResult.latency_ms}ms)`}
                </>
              ) : (
                <>
                  <XCircle className="h-4 w-4" />
                  {testResult.error ?? 'Failed'}
                </>
              )}
            </span>
          )}
        </div>
      )}

      {error != null && <ApiErrorMessage error={error} />}

      <Button type="submit" disabled={isLoading}>
        {isLoading && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
        {submitLabel}
      </Button>
    </form>
  )
}
