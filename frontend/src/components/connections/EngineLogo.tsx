import type { Connection } from '@/lib/api'
import { cn } from '@/lib/utils'

export type ConnectorRole = 'source' | 'destination'
export type SqlDialectHint = 'postgresql' | 'mysql' | 'oracle' | 'mssql' | 'sqlite' | 'generic'

export interface ConnectorMeta {
  type: Connection['type']
  role: ConnectorRole
  title: string
  engineName: string
  formatBadge?: string
  summary: string
  protocolHint: string
  dialect: SqlDialectHint
}

export function detectSqlDialect(nameOrUri = ''): SqlDialectHint {
  const s = nameOrUri.toLowerCase()
  if (
    s.includes('postgres') ||
    s.includes('pgsql') ||
    s.includes('postgresql') ||
    s.includes('pg_') ||
    s.includes('_pg') ||
    s.includes('supabase') ||
    s.includes('neon') ||
    s.includes('cockroach') ||
    s.includes('redshift')
  ) {
    return 'postgresql'
  }
  if (s.includes('mysql') || s.includes('maria') || s.includes('mariadb') || s.includes('tidb') || s.includes('percona')) {
    return 'mysql'
  }
  if (s.includes('oracle') || s.includes('orcl') || s.includes('ora_') || s.includes('_ora')) {
    return 'oracle'
  }
  if (s.includes('mssql') || s.includes('sqlserver') || s.includes('sql_server') || s.includes('azure_sql') || s.includes('tsql')) {
    return 'mssql'
  }
  if (s.includes('sqlite') || s.includes('sqlite3')) {
    return 'sqlite'
  }
  return 'generic'
}

const SQL_DIALECT_LABELS: Record<SqlDialectHint, string> = {
  postgresql: 'PostgreSQL',
  mysql: 'MySQL',
  oracle: 'Oracle',
  mssql: 'SQL Server',
  sqlite: 'SQLite',
  generic: 'SQL Database',
}

export function getConnectorMeta(type: string, connectionName = ''): ConnectorMeta {
  switch (type) {
    case 'sql': {
      const dialect = detectSqlDialect(connectionName)
      return {
        type: 'sql',
        role: 'source',
        title: dialect === 'generic' ? 'SQL Database' : SQL_DIALECT_LABELS[dialect],
        engineName: SQL_DIALECT_LABELS[dialect],
        summary: 'PostgreSQL, MySQL, Oracle, SQL Server',
        protocolHint: 'postgresql:// · mysql:// · oracle:// · mssql://',
        dialect,
      }
    }
    case 'sftp':
      return {
        type: 'sftp',
        role: 'source',
        title: 'SFTP Server',
        engineName: 'SFTP',
        summary: 'CSV, JSON, and Avro files over SSH',
        protocolHint: 'sftp://host:22/path',
        dialect: 'generic',
      }
    case 's3_parquet':
      return {
        type: 's3_parquet',
        role: 'destination',
        title: 'Amazon S3 / MinIO',
        engineName: 'S3 Object Storage',
        formatBadge: 'Parquet',
        summary: 'AWS S3 or S3-compatible MinIO buckets',
        protocolHint: 's3://bronze/dataset/',
        dialect: 'generic',
      }
    case 'gcs_parquet':
      return {
        type: 'gcs_parquet',
        role: 'destination',
        title: 'Google Cloud Storage',
        engineName: 'Google Cloud Storage',
        formatBadge: 'Parquet',
        summary: 'GCS buckets via ADC / Workload Identity or SA JSON',
        protocolHint: 'gs://bronze/dataset/',
        dialect: 'generic',
      }
    default:
      return {
        type: 'sql',
        role: 'source',
        title: type,
        engineName: type,
        summary: '',
        protocolHint: '',
        dialect: 'generic',
      }
  }
}

interface EngineLogoProps {
  type: string
  name?: string
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

export function EngineLogo({ type, name = '', size = 'md', className }: EngineLogoProps) {
  const meta = getConnectorMeta(type, name)

  const boxSizes = {
    sm: 'h-8 w-8 rounded-lg',
    md: 'h-10 w-10 rounded-xl',
    lg: 'h-12 w-12 rounded-xl',
  }

  const iconSizes = {
    sm: 'h-4 w-4',
    md: 'h-5 w-5',
    lg: 'h-6 w-6',
  }

  return (
    <div
      title={meta.engineName}
      className={cn(
        'relative flex items-center justify-center shrink-0 border border-border/80 bg-card shadow-apple-sm select-none',
        boxSizes[size],
        className,
      )}
    >
      {type === 'sql' && <SqlBrandLogo dialect={meta.dialect} className={iconSizes[size]} />}
      {type === 'sftp' && <SftpBrandLogo className={iconSizes[size]} />}
      {type === 's3_parquet' && <S3BrandLogo name={name} className={iconSizes[size]} />}
      {type === 'gcs_parquet' && <GcsBrandLogo className={iconSizes[size]} />}
    </div>
  )
}

/* ── Recognizable Brand Vector Logos ─────────────────────────────────────── */

function SqlBrandLogo({ dialect, className }: { dialect: SqlDialectHint; className?: string }) {
  if (dialect === 'postgresql') {
    // PostgreSQL elephant shield & tusks in official Postgres Blue (#336791)
    return (
      <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
        <path
          d="M12 3.5c-4.4 0-7.5 2.4-7.5 6.2 0 2.4 1.3 4.3 3.2 5.1V18c0 1.4 1.1 2.5 2.5 2.5.9 0 1.7-.5 2.1-1.2.4.7 1.2 1.2 2.1 1.2 1.4 0 2.5-1.1 2.5-2.5v-2.2c1.6-.9 2.6-2.7 2.6-5.1 0-3.8-3.1-7.2-7.5-7.2Z"
          fill="#336791"
          fillOpacity="0.18"
          stroke="#336791"
          strokeWidth="1.65"
          strokeLinejoin="round"
        />
        <path
          d="M12 8.5v7.5c0 1.1.7 2 1.7 2s1.7-.8 1.7-1.8"
          stroke="#336791"
          strokeWidth="1.65"
          strokeLinecap="round"
        />
        <circle cx="9.6" cy="9.8" r="1" fill="#336791" />
        <circle cx="14.6" cy="9.8" r="1" fill="#336791" />
      </svg>
    )
  }

  if (dialect === 'mysql') {
    // MySQL brand dolphin in official teal (#00758F) with orange sun (#F29111)
    return (
      <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
        <path
          d="M4.2 17.2c2-4.6 5.4-8.2 10.2-9.2 1.9-.3 3.9.2 5.4 1.4-1.7 1.2-2.7 2.7-3.1 4.6 1.3.3 2.4 1 3.1 2.1-2.1.4-4-.2-5.6-1.4-2.5 1.4-5 2.5-10 2.5Z"
          fill="#00758F"
          fillOpacity="0.18"
          stroke="#00758F"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
        <circle cx="14.5" cy="10.4" r="1.1" fill="#F29111" />
      </svg>
    )
  }

  if (dialect === 'oracle') {
    // Oracle signature red capsule mark
    return (
      <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
        <rect
          x="3.2"
          y="7.5"
          width="17.6"
          height="9"
          rx="4.5"
          stroke="#F80000"
          strokeWidth="2.1"
          fill="#F80000"
          fillOpacity="0.12"
        />
      </svg>
    )
  }

  if (dialect === 'mssql') {
    // Microsoft SQL Server database cylinder + 4-quadrant Microsoft brand badge
    return (
      <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
        <ellipse
          cx="11"
          cy="6.2"
          rx="6.5"
          ry="2.6"
          fill="#CC292B"
          fillOpacity="0.14"
          stroke="#CC292B"
          strokeWidth="1.6"
        />
        <path
          d="M4.5 6.2v10.5c0 1.44 2.91 2.6 6.5 2.6.7 0 1.38-.04 2-.13"
          stroke="#CC292B"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
        <path
          d="M17.5 6.2v4.8"
          stroke="#CC292B"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
        <path
          d="M4.5 11.4c0 1.44 2.91 2.6 6.5 2.6 1.4 0 2.7-.18 3.8-.5"
          stroke="#CC292B"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
        {/* Microsoft 4-square badge */}
        <rect x="15" y="13" width="3" height="3" rx="0.4" fill="#F25022" />
        <rect x="18.5" y="13" width="3" height="3" rx="0.4" fill="#7FBA00" />
        <rect x="15" y="16.5" width="3" height="3" rx="0.4" fill="#00A4EF" />
        <rect x="18.5" y="16.5" width="3" height="3" rx="0.4" fill="#FFB900" />
      </svg>
    )
  }

  if (dialect === 'sqlite') {
    // SQLite feather quill
    return (
      <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
        <path
          d="M19.5 3.8c-3 1-8.2 4.8-11.2 10.6l-1 4.2 4.2-1c5.8-3 9.6-8.2 10.6-11.2-.4-1.6-1-2.2-2.6-2.6Z"
          fill="#003B57"
          fillOpacity="0.16"
          stroke="#003B57"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
        <path
          d="m7.3 18.6 7-7M10.8 10.3l-2.6.9M14.3 6.8l-2.6.9"
          stroke="#0080CD"
          strokeWidth="1.4"
          strokeLinecap="round"
        />
        <circle cx="5" cy="20.5" r="1.1" fill="#0080CD" />
      </svg>
    )
  }

  // Generic / Default SQL Database — clean relational database cylinder stack
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <ellipse
        cx="12"
        cy="6.2"
        rx="7.5"
        ry="2.8"
        fill="#2563EB"
        fillOpacity="0.14"
        stroke="#2563EB"
        strokeWidth="1.65"
      />
      <path
        d="M4.5 6.2v11.6c0 1.55 3.36 2.8 7.5 2.8s7.5-1.25 7.5-2.8V6.2"
        stroke="#2563EB"
        strokeWidth="1.65"
        strokeLinecap="round"
      />
      <path
        d="M4.5 12c0 1.55 3.36 2.8 7.5 2.8s7.5-1.25 7.5-2.8"
        stroke="#2563EB"
        strokeWidth="1.65"
        strokeLinecap="round"
      />
      <circle cx="12" cy="6.2" r="1.1" fill="#2563EB" />
    </svg>
  )
}

function S3BrandLogo({ name = '', className }: { name?: string; className?: string }) {
  const isMinio = name.toLowerCase().includes('minio')
  const primary = isMinio ? '#C72C48' : '#E25444'
  const secondary = isMinio ? '#E85D75' : '#F58536'

  // AWS S3 / MinIO 3D Storage Cube + Bucket emblem
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path
        d="M12 2.8 20 7.3v9.4L12 21.2 4 16.7V7.3L12 2.8Z"
        fill={primary}
        fillOpacity="0.16"
        stroke={primary}
        strokeWidth="1.65"
        strokeLinejoin="round"
      />
      <path
        d="M4 7.3 12 11.8l8-4.5"
        stroke={secondary}
        strokeWidth="1.65"
        strokeLinejoin="round"
      />
      <path
        d="M12 11.8v9.4"
        stroke={primary}
        strokeWidth="1.65"
        strokeLinecap="round"
      />
      <circle cx="12" cy="7.2" r="1.4" fill={secondary} />
    </svg>
  )
}

function GcsBrandLogo({ className }: { className?: string }) {
  // Google Cloud Storage official multi-color cloud + storage bars
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      {/* Top arc — Google Red */}
      <path
        d="M7.8 9.2A5.3 5.3 0 0 1 17.5 9"
        stroke="#EA4335"
        strokeWidth="2"
        strokeLinecap="round"
      />
      {/* Right arc — Google Blue */}
      <path
        d="M17.5 9a4 4 0 0 1-.3 8H12"
        stroke="#4285F4"
        strokeWidth="2"
        strokeLinecap="round"
      />
      {/* Bottom-left arc — Google Green & Yellow */}
      <path
        d="M12 17H7.2a3.7 3.7 0 0 1-.6-7.35"
        stroke="#34A853"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <path
        d="M6.6 9.65A4 4 0 0 1 7.8 9.2"
        stroke="#FBBC05"
        strokeWidth="2"
        strokeLinecap="round"
      />
      {/* Storage bar inside cloud */}
      <rect x="9.5" y="12.2" width="5" height="2" rx="1" fill="#4285F4" />
    </svg>
  )
}

function SftpBrandLogo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <rect
        x="3.2"
        y="4.5"
        width="17.6"
        height="15"
        rx="2.5"
        fill="#10B981"
        fillOpacity="0.14"
        stroke="#10B981"
        strokeWidth="1.65"
      />
      <path
        d="m7.5 10 2.5 2-2.5 2M12 14h4.5"
        stroke="#10B981"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
