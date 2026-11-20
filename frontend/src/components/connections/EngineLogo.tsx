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
  if (s.includes('postgres') || s.includes('pg') || s.includes('supabase') || s.includes('neon')) {
    return 'postgresql'
  }
  if (s.includes('mysql') || s.includes('maria')) {
    return 'mysql'
  }
  if (s.includes('oracle') || s.includes('ora')) {
    return 'oracle'
  }
  if (s.includes('mssql') || s.includes('sqlserver') || s.includes('azure')) {
    return 'mssql'
  }
  if (s.includes('sqlite')) {
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
  generic: 'PostgreSQL / SQL',
}

export function getConnectorMeta(type: string, connectionName = ''): ConnectorMeta {
  switch (type) {
    case 'sql': {
      const dialect = detectSqlDialect(connectionName)
      return {
        type: 'sql',
        role: 'source',
        title: 'SQL Database',
        engineName: SQL_DIALECT_LABELS[dialect],
        summary: 'PostgreSQL, MySQL, Oracle, SQL Server',
        protocolHint: 'postgresql:// · mysql:// · oracle://',
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
  if (dialect === 'mysql') {
    // MySQL brand teal/orange mark
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
        />
      </svg>
    )
  }

  // Default / PostgreSQL elephant shield + database mark in Postgres Blue (#336791)
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      {/* Postgres blue elephant ear + trunk silhouette */}
      <path
        d="M12 3.5c-4.4 0-7.5 2.4-7.5 6.2 0 2.4 1.3 4.3 3.2 5.1V18c0 1.4 1.1 2.5 2.5 2.5.9 0 1.7-.5 2.1-1.2.4.7 1.2 1.2 2.1 1.2 1.4 0 2.5-1.1 2.5-2.5v-2.2c1.6-.9 2.6-2.7 2.6-5.1 0-3.8-3.1-7.2-7.5-7.2Z"
        fill="#336791"
        fillOpacity="0.18"
        stroke="#336791"
        strokeWidth="1.65"
        strokeLinejoin="round"
      />
      {/* Trunk & tusks */}
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
