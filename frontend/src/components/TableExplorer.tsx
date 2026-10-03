import { useEffect, useState } from 'react'
import { ApiError, api } from '../api'
import { isNumeric } from '../format'
import type { DatasetColumn, DatasetTable, RowPage, SessionDetail } from '../types'

const PAGE_SIZE = 25

interface Props {
  session: SessionDetail | null
  /** Set when the graph view asks for one table's rows, so arriving here from
   *  a node opens that table rather than whichever one sorted first. */
  focusTableId?: string | null
}

function message(cause: unknown): string {
  return cause instanceof ApiError ? cause.message : String(cause)
}

export function TableExplorer({ session, focusTableId }: Props) {
  const [tables, setTables] = useState<DatasetTable[]>([])
  const [selected, setSelected] = useState<DatasetTable | null>(null)
  const [page, setPage] = useState<RowPage | null>(null)
  const [offset, setOffset] = useState(0)
  const [loadingRows, setLoadingRows] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const sessionId = session?.session_id ?? null
  const status = session?.status

  useEffect(() => {
    setSelected(null)
    setPage(null)
    setOffset(0)
    setError(null)
    if (!sessionId) {
      setTables([])
      return
    }

    // Tables only exist once the upload has been parsed, so this re-runs when
    // the status changes rather than only when a different upload is picked.
    let active = true
    api
      .getTables(sessionId)
      .then((result) => {
        if (!active) return
        setTables(result.tables)
        // With one table there is nothing to choose between, and with several
        // the first one is a better starting point than an empty panel.
        setSelected(result.tables[0] ?? null)
      })
      .catch((cause) => active && setError(message(cause)))

    return () => {
      active = false
    }
  }, [sessionId, status])

  // Applied separately from the fetch above because the two can arrive in
  // either order: the tables may already be loaded when the graph asks for one.
  useEffect(() => {
    if (!focusTableId) return
    const wanted = tables.find((table) => table.table_id === focusTableId)
    if (wanted) {
      setSelected(wanted)
      setOffset(0)
    }
  }, [focusTableId, tables])

  useEffect(() => {
    if (!selected) {
      setPage(null)
      return
    }

    // The previous page stays on screen, dimmed, until the next one arrives —
    // clearing it first would collapse the panel and shift everything below.
    let active = true
    setLoadingRows(true)
    api
      .getRows(selected.table_id, offset, PAGE_SIZE)
      .then((result) => active && setPage(result))
      .catch((cause) => active && setError(message(cause)))
      .finally(() => active && setLoadingRows(false))

    return () => {
      active = false
    }
  }, [selected, offset])

  if (!session) {
    return <p className="empty">Pick an upload to see what it parsed into.</p>
  }

  const pending = session.status === 'queued' || session.status === 'processing'

  return (
    <>
      {session.status === 'failed' && (
        <p className="error">{session.error_message ?? 'This upload failed.'}</p>
      )}
      {pending && (
        <p className="waiting">
          <span className="spinner" />
          <span>Still being parsed…</span>
        </p>
      )}
      {error && <p className="error">{error}</p>}

      {tables.length > 1 && (
        <div className="tabs">
          {tables.map((table) => (
            <button
              key={table.table_id}
              type="button"
              className={table.table_id === selected?.table_id ? 'tab selected' : 'tab'}
              onClick={() => {
                setSelected(table)
                setOffset(0)
              }}
            >
              {table.name}
              <span className="count">{table.row_count.toLocaleString()} rows</span>
            </button>
          ))}
        </div>
      )}

      {selected && (
        <div className="fade-in" key={selected.table_id}>
          {tables.length === 1 && (
            <p className="hint">
              <strong>{selected.name}</strong> · {selected.row_count.toLocaleString()} rows ·{' '}
              {selected.columns.length} columns
            </p>
          )}

          <div className="columns">
            {selected.columns.map((column) => (
              <span key={column.name} className="column">
                {column.name}
                <span className="type">{column.inferred_type}</span>
              </span>
            ))}
          </div>

          {page && page.total_rows === 0 && <p className="empty">This table has no rows.</p>}

          {page && page.total_rows > 0 && (
            <>
              <RowTable table={selected} page={page} loading={loadingRows} />
              <div className="paging">
                <button
                  type="button"
                  disabled={offset === 0 || loadingRows}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                >
                  Previous
                </button>
                <span className="hint">
                  {offset + 1}–{Math.min(offset + PAGE_SIZE, page.total_rows)} of{' '}
                  {page.total_rows.toLocaleString()}
                </span>
                <button
                  type="button"
                  disabled={offset + PAGE_SIZE >= page.total_rows || loadingRows}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                >
                  Next
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {!pending && session.status === 'ready' && tables.length === 0 && (
        <p className="empty">This upload produced no tables.</p>
      )}
    </>
  )
}

interface RowTableProps {
  table: DatasetTable
  page: RowPage
  loading: boolean
}

function RowTable({ table, page, loading }: RowTableProps) {
  return (
    <div className={loading ? 'grid loading' : 'grid'}>
      <table>
        <thead>
          <tr>
            {table.columns.map((column) => (
              <th key={column.name} className={cellClass(column)}>
                {column.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {page.rows.map((row, index) => (
            <tr key={page.offset + index}>
              {table.columns.map((column) => (
                <td key={column.name} className={cellClass(column)}>
                  {render(row[column.name])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function cellClass(column: DatasetColumn): string | undefined {
  return isNumeric(column.inferred_type) ? 'numeric' : undefined
}

/** A missing value is shown as such rather than as an empty cell. */
function render(value: unknown) {
  if (value === null || value === undefined) return <span className="null">null</span>
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
