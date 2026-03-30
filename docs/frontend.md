# Frontend

The Siphon UI is a React single-page application (SPA) bundled with Vite and served as static files by the FastAPI backend. There is no separate frontend server in production.

---

## Tech stack

| Library | Version | Purpose |
|---|---|---|
| React | 18 | UI framework |
| TypeScript | 5 | Type safety |
| Vite | 5 | Build tool + dev server |
| TanStack React Query | 5 | Server state, caching, background refetch |
| Tailwind CSS | 3 | Utility-first CSS |
| shadcn/ui | — | Accessible component library (Radix UI + Tailwind) |
| React Router | 6 | Client-side routing |
| Axios | — | HTTP client with interceptors |
| react-hook-form + zod | — | Form state + schema validation |
| date-fns | — | Date formatting |
| lucide-react | — | Icons |
| CodeMirror | 6 | SQL query editor |

---

## Directory structure

```
frontend/src/
├── main.tsx                  Entry point: React root + providers
├── App.tsx                   Router + route definitions
├── index.css                 Tailwind base styles
├── contexts/
│   └── AuthContext.tsx        Auth state (user, login, logout, loading)
├── lib/
│   ├── api.ts                 Axios client + all typed API methods
│   ├── queryKeys.ts           React Query key factory
│   └── utils.ts               cn() helper (Tailwind class merging)
├── pages/
│   ├── DashboardPage.tsx
│   ├── LoginPage.tsx
│   ├── connections/
│   │   ├── ConnectionsPage.tsx
│   │   ├── ConnectionNewPage.tsx
│   │   ├── ConnectionDetailPage.tsx
│   │   └── ConnectionEditPage.tsx
│   ├── pipelines/
│   │   ├── PipelinesPage.tsx
│   │   ├── PipelineWizard.tsx   (multi-step new pipeline form)
│   │   ├── PipelineDetailPage.tsx
│   │   └── PipelineEditPage.tsx
│   ├── runs/
│   │   ├── RunsPage.tsx
│   │   └── RunDetailPage.tsx
│   └── settings/
│       ├── UsersPage.tsx
│       └── SystemPage.tsx
├── components/
│   ├── layout/
│   │   ├── AppLayout.tsx        Sidebar + main content area
│   │   └── RequireAuth.tsx      Auth guard: redirects to /login if not authenticated
│   ├── shared/
│   │   ├── PageHeader.tsx
│   │   ├── StatusBadge.tsx      Colored badge for job status
│   │   ├── SchemaDriftBadge.tsx
│   │   ├── ConnectionSelect.tsx  Dropdown populated from API
│   │   ├── QueryEditor.tsx       CodeMirror SQL editor (lazy-loaded)
│   │   ├── CronInput.tsx         5-field cron expression input
│   │   ├── ConfirmDialog.tsx      Modal confirm prompt
│   │   ├── ApiErrorMessage.tsx    Formats HTTP errors for display
│   │   └── EmptyState.tsx
│   ├── connections/
│   │   └── ConnectionForm.tsx    Reusable create/edit form (dynamic fields)
│   ├── runs/
│   │   └── LogViewer.tsx         Real-time log polling component
│   └── ui/                       shadcn/ui primitives (Button, Input, Card…)
```

---

## Authentication flow

The frontend never stores tokens in `localStorage` or `sessionStorage`. The access token lives only in memory (a module-level variable in `api.ts`). This prevents XSS attacks from stealing tokens.

```
App loads
    │
    ▼
AuthContext mounts → POST /api/v1/auth/refresh
    │
    ├── Success → sets access token in memory → GET /api/v1/auth/me → sets user state
    └── Failure → user = null, loading = false
    │
    ▼
RequireAuth checks { user, loading }
    ├── loading = true → shows spinner
    ├── user = null   → <Navigate to="/login" />
    └── user set      → renders children (AppLayout + page)
```

**Token refresh during navigation:**

When any API call returns 401, Axios interceptors automatically call `POST /api/v1/auth/refresh`, update the in-memory token, and retry the original request — invisible to the user. If the refresh also fails, the user is redirected to `/login`.

Multiple concurrent 401s are deduplicated: only one refresh call is made, and all waiting requests get the new token.

---

## API client (`lib/api.ts`)

The API client is a single Axios instance with typed methods for every endpoint.

```typescript
import { connectionsApi, pipelinesApi, runsApi } from '@/lib/api'

// All methods return Axios responses with typed data
const { data: connections } = await connectionsApi.list()
const { data: pipeline }    = await pipelinesApi.get(id)
const { data: runs }        = await runsApi.list({ limit: 10 })
```

**Available method groups:**

| Export | Endpoints covered |
|---|---|
| `authApi` | login, refresh, logout, me |
| `connectionsApi` | list, get, create, update, delete, test, testById, types |
| `pipelinesApi` | list, get, create, update, delete, trigger, runs, setSchedule, deleteSchedule |
| `runsApi` | list, get, logs, cancel |
| `previewApi` | run |
| `usersApi` | list, get, create, update, delete |

---

## Server state with React Query

All API data is managed by TanStack React Query. Components never call the API directly; they declare what data they need and React Query handles caching, background refetching, and invalidation.

```typescript
// Reading data
const { data: pipelines, isLoading } = useQuery({
  queryKey: queryKeys.pipelines.list(1, 100),
  queryFn: () => pipelinesApi.list().then(r => r.data),
})

// Writing data
const mutation = useMutation({
  mutationFn: (data) => pipelinesApi.create(data),
  onSuccess: () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.pipelines.all })
    toast({ title: 'Pipeline created' })
    navigate('/pipelines')
  },
})
```

**Query key factory (`lib/queryKeys.ts`):**

All query keys are centralized to prevent typos and enable targeted invalidation:

```typescript
queryKeys.connections.all                  // ['connections']
queryKeys.connections.detail(id)           // ['connections', id]
queryKeys.pipelines.list(page, limit)      // ['pipelines', 'list', 1, 100]
queryKeys.runs.list({ limit: 10 })         // ['runs', 'list', { limit: 10 }]
queryKeys.runs.logs(id)                    // ['runs', id, 'logs']
```

---

## Pages

### Dashboard
Shows three summary cards (active pipelines, successful runs, failed runs) and a list of the 10 most recent runs. Refetches every 10 seconds.

### Login
Email + password form. Calls `authApi.login()`, then stores the access token and navigates to `/`.

### Connections list
Table of all connections with type badge and timestamps. Links to detail page.

### Connection detail
Read-only view of connection metadata. **Test** button calls `testById` and shows latency. **Edit** navigates to the edit form. **Delete** opens a confirm dialog.

### Connection new / edit
Both use the shared `ConnectionForm` component. The form loads available types from `GET /api/v1/connections/types/list` and renders the appropriate fields dynamically. In edit mode, the type is locked (shown as read-only text) and all credential fields show a "leave blank to keep current" placeholder.

### Pipeline wizard (new pipeline)
4-step wizard:

1. **Source** — select source connection (filtered to `sql` type)
2. **Query & Preview** — write SQL, optionally run Preview to see 100 rows; set extraction mode
3. **Destination** — select destination connection (filtered to `s3_parquet`); set S3 prefix; set optional DQ rules
4. **Schedule & Review** — name the pipeline; optionally add cron schedule; review summary; create

### Pipeline detail
Shows configuration, schedule (with edit/add/remove), and last 10 runs. **Run Now** triggers immediately and navigates to the new run's detail page.

### Pipeline edit
Full edit form with the same fields as the wizard, but without the wizard UX. Useful for updating the query or DQ rules on an existing pipeline.

### Runs list
Global run history (all pipelines) with status filter dropdown. Auto-refreshes every 10 seconds.

### Run detail
Status card, metadata (rows, duration, error), and the **LogViewer**. Shows a **Cancel** button for queued runs. Links to the parent pipeline if available.

### Settings → Users
Admin-only. Table of all users with role and active status. Create, edit role/password, and deactivate users.

---

## Key components

### `ConnectionForm`

Reusable form for creating and editing connections. Dynamically renders the correct credential fields based on the selected type, using the schema from `GET /api/v1/connections/types/list`.

Props:
```typescript
interface ConnectionFormProps {
  initialValues?: Record<string, unknown>
  editing?: boolean           // locks type, changes field placeholders
  onSubmit: (data) => Promise<void>
  submitLabel?: string
  isLoading?: boolean
  error?: unknown
}
```

### `ConnectionSelect`

Dropdown populated from the connections API. Accepts a `filterType` prop to show only connections of a given type.

```tsx
<ConnectionSelect
  value={selectedId}
  onChange={setSelectedId}
  filterType="sql"          // "sql" | "sftp" | "s3_parquet"
/>
```

### `LogViewer`

Polls `GET /api/v1/runs/{id}/logs?since=N` every 2 seconds while the run is active. Stops polling once the run reaches a terminal state. New lines are appended without replacing existing ones.

```tsx
<LogViewer runId={run.id} status={run.status} />
```

### `QueryEditor`

Lazy-loaded CodeMirror SQL editor. Falls back to a plain `<textarea>` while the bundle is loading (via `React.Suspense`).

```tsx
<Suspense fallback={<textarea value={query} onChange={...} />}>
  <QueryEditor value={query} onChange={setQuery} />
</Suspense>
```

### `StatusBadge`

Renders a colored badge based on job status.

| Status | Color |
|---|---|
| `queued` | Gray |
| `running` | Blue (animated) |
| `success` | Green |
| `failed` | Red |
| `partial_success` | Orange |

### `CronInput`

Single text input that accepts a 5-field cron expression. Displays a human-readable interpretation below (e.g. "Every day at 3:00 AM").

---

## Local development

The frontend dev server runs separately from the backend and proxies API calls.

```bash
cd frontend
pnpm install
pnpm dev        # starts Vite on http://localhost:5173
```

API calls are proxied to `http://localhost:8000` via `vite.config.ts`. The `VITE_API_BASE_URL` environment variable is empty by default, meaning all API calls use a relative path (correct in production where the frontend and API share the same origin).

**Hot module replacement (HMR)** is fully enabled in dev mode. The backend does not need to be rebuilt when frontend files change.

---

## Building for production

The Dockerfile builds the frontend automatically:

```bash
# In the frontend-builder stage:
pnpm install --frozen-lockfile
pnpm build      # outputs to frontend/dist/
```

The `dist/` directory is copied into the final image and served by FastAPI's `StaticFiles` mount at `/`. React Router handles client-side navigation; unknown paths fall back to `index.html`.

To build and serve locally without Docker:

```bash
cd frontend
pnpm build
# Then run the backend — it will serve frontend/dist automatically
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `VITE_API_BASE_URL` | `""` (relative) | API base URL. Leave empty in production (same origin). Set to `http://localhost:8000` only if the dev server runs on a different port than the API |

---

## Adding a new page

1. Create the component in `frontend/src/pages/<section>/MyPage.tsx`
2. Add the route in `App.tsx` inside the protected route block:
   ```tsx
   <Route path="my-section" element={<MyPage />} />
   ```
3. If it needs data, add typed API methods to `lib/api.ts` and a query key to `lib/queryKeys.ts`
4. Add a nav link in `components/layout/AppLayout.tsx` if it should appear in the sidebar
