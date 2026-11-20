import { ArrowDownToLine, ArrowUpFromLine } from 'lucide-react'
import type { Connection } from '@/lib/api'
import { cn } from '@/lib/utils'

export type ConnectorRole = 'source' | 'destination'
export type SqlDialectHint = 'postgresql' | 'mysql' | 'oracle' | 'mssql' | 'sqlite' | 'generic'

export interface ConnectorMeta {
  type: Connection['type']
  role: ConnectorRole
  roleLabel: string
  title: string
  engineName: string
  shortTag: string
  summary: string
  capabilities: string
  directionRule: string
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
  if (s.includes('mssql') || s.includes('sqlserver') || s.includes('azure-sql')) {
    return 'mssql'
  }
  if (s.includes('sqlite')) {
    return 'sqlite'
  }
  return 'generic'
}

const SQL_DIALECT_LABELS: Record<SqlDialectHint, string> = {
  postgresql: 'PostgreSQL',
  mysql: 'MySQL / MariaDB',
  oracle: 'Oracle DB',
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
        roleLabel: 'Source · Read-Only',
        title: 'SQL Database',
        engineName: SQL_DIALECT_LABELS[dialect],
        shortTag: 'SQL',
        summary: 'Extract tables or custom SQL queries via ConnectorX / cursor streaming.',
        capabilities: 'Full refresh, incremental watermark, and date-window backfill',
        directionRule:
          'Strictly a read-only extraction source. Siphon does not write back to SQL databases (no Reverse ETL).',
        protocolHint: 'postgresql:// · mysql:// · oracle:// · mssql://',
        dialect,
      }
    }
    case 'sftp':
      return {
        type: 'sftp',
        role: 'source',
        roleLabel: 'Source · Read-Only',
        title: 'SFTP Server',
        engineName: 'SSH File Transfer',
        shortTag: 'SFTP',
        summary: 'Ingest CSV, JSON/JSONL, and Avro batch files over SSH.',
        capabilities: 'Batch file streaming, glob patterns, and processed-folder archiving',
        directionRule:
          'Strictly a read-only file source. Siphon extracts remote files and converts them to Bronze Parquet.',
        protocolHint: 'sftp://host:22/path/*.csv',
        dialect: 'generic',
      }
    case 's3_parquet':
      return {
        type: 's3_parquet',
        role: 'destination',
        roleLabel: 'Destination · Bronze Parquet',
        title: 'Amazon S3 / MinIO',
        engineName: 'S3 Object Storage',
        shortTag: 'S3',
        summary: 'Write Snappy-compressed Parquet datasets to AWS S3 or MinIO.',
        capabilities: 'Atomic _staging promote, Hive _date partitioning, and GDPR purge',
        directionRule:
          'Write-only Bronze destination. Receives extracted Arrow batches as Parquet files (cannot be used as a pipeline source).',
        protocolHint: 's3://bronze/<dataset>/',
        dialect: 'generic',
      }
    case 'gcs_parquet':
      return {
        type: 'gcs_parquet',
        role: 'destination',
        roleLabel: 'Destination · Bronze Parquet',
        title: 'Google Cloud Storage',
        engineName: 'GCP Cloud Storage',
        shortTag: 'GCS',
        summary: 'Write Snappy-compressed Parquet datasets to Google Cloud Storage buckets.',
        capabilities: 'ADC / GCP VM Metadata Server, Service Account JSON, atomic staging, and GDPR purge',
        directionRule:
          'Write-only Bronze destination. Receives extracted Arrow batches as Parquet files on gs:// (cannot be used as a pipeline source).',
        protocolHint: 'gs://bronze/<dataset>/',
        dialect: 'generic',
      }
    default:
      return {
        type: 'sql',
        role: 'source',
        roleLabel: 'Source',
        title: type,
        engineName: type,
        shortTag: type.toUpperCase(),
        summary: 'Data connector',
        capabilities: '',
        directionRule: '',
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
      title={`${meta.engineName} (${meta.roleLabel})`}
      className={cn(
        'relative flex items-center justify-center shrink-0 border border-border/80 bg-secondary/70 text-foreground shadow-apple-sm select-none',
        boxSizes[size],
        className,
      )}
    >
      {type === 'sql' && <SqlEngineSvg dialect={meta.dialect} className={iconSizes[size]} />}
      {type === 'sftp' && <SftpEngineSvg className={iconSizes[size]} />}
      {type === 's3_parquet' && <S3EngineSvg className={iconSizes[size]} />}
      {type === 'gcs_parquet' && <GcsEngineSvg className={iconSizes[size]} />}
    </div>
  )
}

export function ConnectionRoleBadge({
  type,
  className,
}: {
  type: string
  className?: string
}) {
  const meta = getConnectorMeta(type)
  const isSource = meta.role === 'source'

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[10.5px] font-medium tracking-tight',
        isSource
          ? 'border-sky-500/25 bg-sky-500/10 text-sky-700 dark:text-sky-300'
          : 'border-emerald-500/25 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
        className,
      )}
    >
      {isSource ? (
        <ArrowUpFromLine className="h-3 w-3 shrink-0" />
      ) : (
        <ArrowDownToLine className="h-3 w-3 shrink-0" />
      )}
      {isSource ? 'Source · Extract' : 'Destination · Parquet'}
    </span>
  )
}

/* ── Distinct Engine Vector Emblems ──────────────────────────────────────── */

function SqlEngineSvg({ dialect, className }: { dialect: SqlDialectHint; className?: string }) {
  if (dialect === 'postgresql') {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
        <ellipse cx="12" cy="5.5" rx="7" ry="2.8" stroke="currentColor" strokeWidth="1.7" />
        <path d="M5 5.5v6c0 1.55 3.13 2.8 7 2.8s7-1.25 7-2.8v-6" stroke="currentColor" strokeWidth="1.7" />
        <path d="M5 11.5v6c0 1.55 3.13 2.8 7 2.8s7-1.25 7-2.8v-6" stroke="currentColor" strokeWidth="1.7" />
        <circle cx="15.5" cy="11.2" r="1" fill="currentColor" />
      </svg>
    )
  }
  if (dialect === 'mysql') {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
        <path
          d="M4 17.5c2.2-4.8 5.6-8.5 10.5-9.5 1.8-.3 3.8.2 5.5 1.5-1.8 1.2-2.8 2.8-3.2 4.8 1.4.3 2.5 1 3.2 2.2-2.2.4-4.2-.2-5.8-1.5-2.6 1.5-5.2 2.5-10.2 2.5Z"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinejoin="round"
        />
        <circle cx="14.5" cy="10.5" r="1" fill="currentColor" />
      </svg>
    )
  }
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <ellipse cx="12" cy="5.5" rx="7" ry="2.7" stroke="currentColor" strokeWidth="1.7" />
      <path d="M5 5.5v6.2c0 1.5 3.13 2.7 7 2.7s7-1.2 7-2.7V5.5" stroke="currentColor" strokeWidth="1.7" />
      <path d="M5 11.7v6.3c0 1.5 3.13 2.7 7 2.7s7-1.2 7-2.7v-6.3" stroke="currentColor" strokeWidth="1.7" />
    </svg>
  )
}

function SftpEngineSvg({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <rect x="3.5" y="4.5" width="17" height="6" rx="1.8" stroke="currentColor" strokeWidth="1.7" />
      <rect x="3.5" y="13.5" width="17" height="6" rx="1.8" stroke="currentColor" strokeWidth="1.7" />
      <circle cx="7" cy="7.5" r="1" fill="currentColor" />
      <circle cx="7" cy="16.5" r="1" fill="currentColor" />
      <path d="M11.5 7.5h5.5M11.5 16.5h5.5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  )
}

function S3EngineSvg({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path
        d="M12 3.2 19.5 7.5v9L12 20.8 4.5 16.5v-9L12 3.2Z"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
      <path d="M4.5 7.5 12 12l7.5-4.5M12 12v8.8" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
    </svg>
  )
}

function GcsEngineSvg({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path
        d="M7.2 18.5h9.8a4.3 4.3 0 0 0 .6-8.55A5.8 5.8 0 0 0 6.3 8.3 3.9 3.9 0 0 0 7.2 18.5Z"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
      <path d="M9.5 13.5h5M9.5 16h3.2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}
