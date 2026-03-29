import { AlertCircle } from 'lucide-react'
import type { AxiosError } from 'axios'

interface ApiErrorMessageProps {
  error: unknown
}

export function ApiErrorMessage({ error }: ApiErrorMessageProps) {
  const axiosError = error as AxiosError<{ detail?: string | { msg: string }[] }>
  let message = 'An unexpected error occurred.'

  if (axiosError?.response?.data?.detail) {
    const detail = axiosError.response.data.detail
    if (typeof detail === 'string') {
      message = detail
    } else if (Array.isArray(detail)) {
      message = detail.map((d) => d.msg).join(', ')
    }
  } else if (axiosError?.message) {
    message = axiosError.message
  }

  return (
    <div className="flex items-center gap-2 rounded-md bg-destructive/10 border border-destructive/20 px-3 py-2 text-sm text-destructive">
      <AlertCircle className="h-4 w-4 shrink-0" />
      <span>{message}</span>
    </div>
  )
}
