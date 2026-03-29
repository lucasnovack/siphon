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
  onSubmit: (data: Record<string, unknown>) => Promise<void>
  submitLabel?: string
  isLoading?: boolean
  error?: unknown
}

export function ConnectionForm({ initialValues, onSubmit, submitLabel = 'Save', isLoading, error }: ConnectionFormProps) {
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
    setDynamicFields({})
  }

  function handleDynamicChange(fieldName: string, value: string) {
    setDynamicFields((prev) => ({ ...prev, [fieldName]: value }))
  }

  async function handleTest() {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await connectionsApi.test({ type: selectedType, ...dynamicFields })
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

  return (
    <form onSubmit={handleSubmit(handleFormSubmit)} className="space-y-4">
      <div className="space-y-2">
        <Label>Name</Label>
        <Input placeholder="my-postgres-prod" {...register('name')} />
        {errors.name && <p className="text-xs text-destructive">{errors.name.message}</p>}
      </div>

      <div className="space-y-2">
        <Label>Type</Label>
        <Select value={selectedType} onValueChange={handleTypeChange}>
          <SelectTrigger className={errors.type ? 'border-destructive' : ''}>
            <SelectValue placeholder="Select type" />
          </SelectTrigger>
          <SelectContent>
            {(types ?? []).map((t: ConnectionType) => (
              <SelectItem key={t.type} value={t.type}>
                {t.type}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {errors.type && <p className="text-xs text-destructive">{errors.type.message}</p>}
      </div>

      {currentTypeDef?.fields.map((field) => (
        <div key={field.name} className="space-y-2">
          <Label>
            {field.name}
            {field.required && <span className="text-destructive ml-1">*</span>}
          </Label>
          <Input
            type={field.name.toLowerCase().includes('password') || field.name.toLowerCase().includes('secret') ? 'password' : 'text'}
            placeholder={field.name}
            value={dynamicFields[field.name] ?? ''}
            onChange={(e) => handleDynamicChange(field.name, e.target.value)}
            required={field.required}
          />
        </div>
      ))}

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
