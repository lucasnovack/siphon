import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

export const api = axios.create({
  baseURL: BASE_URL,
  withCredentials: true,
})

// In-memory access token (never stored in localStorage)
let accessToken: string | null = null

export function setAccessToken(token: string | null) {
  accessToken = token
}

export function getAccessToken() {
  return accessToken
}

// Attach Authorization header on every request
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`
  }
  return config
})

// Singleton mutex for concurrent 401s
let refreshPromise: Promise<string> | null = null

async function doRefresh(): Promise<string> {
  const res = await axios.post<{ access_token: string }>(
    `${BASE_URL}/api/v1/auth/refresh`,
    {},
    { withCredentials: true },
  )
  return res.data.access_token
}

api.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const original = error.config as InternalAxiosRequestConfig & { _retry?: boolean }

    // Only retry 401s once, and not on auth endpoints
    if (
      error.response?.status === 401 &&
      !original._retry &&
      !original.url?.includes('/auth/login') &&
      !original.url?.includes('/auth/refresh')
    ) {
      original._retry = true

      if (!refreshPromise) {
        refreshPromise = doRefresh().finally(() => {
          refreshPromise = null
        })
      }

      try {
        const newToken = await refreshPromise
        setAccessToken(newToken)
        original.headers.Authorization = `Bearer ${newToken}`
        return api(original)
      } catch {
        setAccessToken(null)
        window.location.href = '/login'
      }
    }

    return Promise.reject(error)
  },
)

// ─── Typed API helpers ────────────────────────────────────────────────────────

export interface ListEnvelope<T> {
  items: T[]
  total: number
  page: number
  limit: number
}

export interface Connection {
  id: string
  name: string
  type: 'sql' | 'sftp' | 's3_parquet'
  key_version: number
  created_at: string
  updated_at: string
}

export interface ConnectionTypeField {
  name: string
  type: string
  label?: string
  required?: boolean
  secret?: boolean
  placeholder?: string
}

export interface ConnectionType {
  type: string
  fields: ConnectionTypeField[]
}

export interface Pipeline {
  id: string
  name: string
  source_connection_id: string
  dest_connection_id: string | null
  query: string
  destination_path: string
  extraction_mode: 'full_refresh' | 'incremental'
  incremental_key: string | null
  last_watermark: string | null
  last_schema_hash: string | null
  min_rows_expected: number | null
  max_rows_drop_pct: number | null
  pii_columns: Record<string, 'sha256' | 'redact'> | null
  webhook_url: string | null
  alert_on: string[] | null
  sla_minutes: number | null
  partition_by: 'none' | 'ingest_date'
  is_active: boolean
  created_at: string
  updated_at: string
  schedule?: Schedule | null
}

export interface Schedule {
  id: string
  pipeline_id: string
  cron_expr: string
  is_active: boolean
  last_run_at: string | null
  next_run_at: string | null
  created_at: string
}

export interface JobRun {
  id: string
  pipeline_id: string | null
  schedule_id: string | null
  triggered_by: 'schedule' | 'manual' | 'api'
  status: 'queued' | 'running' | 'success' | 'failed' | 'partial_success'
  rows_read: number | null
  rows_written: number | null
  error: string | null
  schema_hash: string | null
  schema_changed: boolean
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export interface User {
  id: string
  email: string
  name: string | null
  role: 'admin' | 'operator'
  is_active: boolean
  created_at: string
}

export interface LogResponse {
  logs: string[]
  next_offset: number
}

export interface PreviewResponse {
  columns: string[]
  rows: unknown[][]
  row_count: number
}

export interface TestConnectionResponse {
  ok: boolean
  latency_ms: number
  error?: string
}

// Auth
export const authApi = {
  login: (email: string, password: string) =>
    api.post<{ access_token: string; expires_in: number }>('/api/v1/auth/login', { email, password }),
  refresh: () =>
    api.post<{ access_token: string; expires_in: number }>('/api/v1/auth/refresh'),
  logout: () => api.post('/api/v1/auth/logout'),
  me: () => api.get<User>('/api/v1/auth/me'),
}

// Connections
export const connectionsApi = {
  list: () =>
    api.get<Connection[]>('/api/v1/connections'),
  get: (id: string) => api.get<Connection>(`/api/v1/connections/${id}`),
  create: (data: unknown) => api.post<Connection>('/api/v1/connections', data),
  update: (id: string, data: unknown) => api.put<Connection>(`/api/v1/connections/${id}`, data),
  delete: (id: string) => api.delete(`/api/v1/connections/${id}`),
  test: (data: unknown) => api.post<TestConnectionResponse>('/api/v1/connections/test', data),
  testById: (id: string) => api.post<TestConnectionResponse>(`/api/v1/connections/${id}/test`),
  types: () => api.get<ConnectionType[]>('/api/v1/connections/types/list'),
}

// Pipelines
export const pipelinesApi = {
  list: () =>
    api.get<Pipeline[]>('/api/v1/pipelines'),
  get: (id: string) => api.get<Pipeline>(`/api/v1/pipelines/${id}`),
  create: (data: unknown) => api.post<Pipeline>('/api/v1/pipelines', data),
  update: (id: string, data: unknown) => api.put<Pipeline>(`/api/v1/pipelines/${id}`, data),
  delete: (id: string) => api.delete(`/api/v1/pipelines/${id}`),
  trigger: (id: string, data?: { date_from?: string; date_to?: string }) =>
    api.post<{ job_id: string }>(`/api/v1/pipelines/${id}/trigger`, data ?? {}),
  runs: (id: string, limit = 10) =>
    api.get<JobRun[]>(`/api/v1/pipelines/${id}/runs`, { params: { limit } }),
  setSchedule: (id: string, data: { cron_expr: string; is_active: boolean }) =>
    api.put<Schedule>(`/api/v1/pipelines/${id}/schedule`, data),
  deleteSchedule: (id: string) => api.delete(`/api/v1/pipelines/${id}/schedule`),
}

// Runs
export const runsApi = {
  list: (params?: { limit?: number; offset?: number; status?: string; pipeline_id?: string }) =>
    api.get<JobRun[]>('/api/v1/runs', { params }),
  get: (id: string) => api.get<JobRun>(`/api/v1/runs/${id}`),
  logs: (id: string, since?: number) =>
    api.get<LogResponse>(`/api/v1/runs/${id}/logs`, { params: since != null ? { since } : {} }),
  cancel: (id: string) => api.post<{ ok: boolean }>(`/api/v1/runs/${id}/cancel`),
}

// Preview
export const previewApi = {
  run: (connection_id: string, query: string) =>
    api.post<PreviewResponse>('/api/v1/preview', { connection_id, query }),
}

// Users
export const usersApi = {
  list: () => api.get<User[]>('/api/v1/users'),
  create: (data: unknown) => api.post<User>('/api/v1/users', data),
  update: (id: string, data: unknown) => api.put<User>(`/api/v1/users/${id}`, data),
  delete: (id: string) => api.delete(`/api/v1/users/${id}`),
}
