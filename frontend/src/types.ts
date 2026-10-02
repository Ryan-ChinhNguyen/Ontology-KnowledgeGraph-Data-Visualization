export type SessionStatus = 'uploading' | 'queued' | 'processing' | 'ready' | 'failed'
export type JobStatus = 'queued' | 'processing' | 'done' | 'failed'
export type FileFormat = 'csv' | 'json' | 'sql' | 'parquet'

export interface SessionSummary {
  session_id: string
  format: FileFormat
  status: SessionStatus
  total_files: number
  total_size_bytes: number
  filenames: string[]
  created_at: string
}

export interface SessionList {
  total: number
  sessions: SessionSummary[]
}

export interface SessionDetail {
  session_id: string
  format: FileFormat
  status: SessionStatus
  total_files: number
  total_size_bytes: number
  job_status: JobStatus | null
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface DatasetColumn {
  name: string
  position: number
  inferred_type: string
}

export interface DatasetTable {
  table_id: string
  name: string
  row_count: number
  columns: DatasetColumn[]
}

export interface DatasetTables {
  session_id: string
  tables: DatasetTable[]
}

export interface RowPage {
  table_id: string
  offset: number
  limit: number
  total_rows: number
  rows: Record<string, unknown>[]
}

export interface UploadResult {
  session_id: string
  job_id: string
  status: SessionStatus
  total_files: number
  total_size_bytes: number
}
