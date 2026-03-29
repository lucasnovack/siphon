export const queryKeys = {
  auth: {
    me: ['auth', 'me'] as const,
  },
  connections: {
    all: ['connections'] as const,
    list: (page: number, limit: number) => ['connections', 'list', page, limit] as const,
    detail: (id: string) => ['connections', id] as const,
    types: ['connections', 'types'] as const,
  },
  pipelines: {
    all: ['pipelines'] as const,
    list: (page: number, limit: number) => ['pipelines', 'list', page, limit] as const,
    detail: (id: string) => ['pipelines', id] as const,
    runs: (id: string, page: number, limit: number) => ['pipelines', id, 'runs', page, limit] as const,
  },
  runs: {
    all: ['runs'] as const,
    list: (params: Record<string, unknown>) => ['runs', 'list', params] as const,
    detail: (id: string) => ['runs', id] as const,
    logs: (id: string) => ['runs', id, 'logs'] as const,
  },
  users: {
    all: ['users'] as const,
  },
}
