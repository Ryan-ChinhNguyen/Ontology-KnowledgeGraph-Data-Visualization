import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from './api'
import { SessionList } from './components/SessionList'
import { TableExplorer } from './components/TableExplorer'
import { UploadPanel } from './components/UploadPanel'
import type { SessionDetail, SessionSummary } from './types'

const POLL_MS = 1000

/** Parsing happens in the background, so these are not final yet. */
function isPending(status: string | undefined): boolean {
  return status === 'uploading' || status === 'queued' || status === 'processing'
}

function message(cause: unknown): string {
  return cause instanceof ApiError ? cause.message : String(cause)
}

export default function App() {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setSessions((await api.listSessions()).sessions)
    } catch (cause) {
      setError(message(cause))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      return
    }

    let active = true
    const read = async () => {
      try {
        const current = await api.getSession(selectedId)
        if (active) setDetail(current)
        return current.status
      } catch (cause) {
        if (active) setError(message(cause))
        return undefined
      }
    }

    // Poll while the upload is still being worked on, and stop once it
    // settles — the API answers status questions, not the other way round.
    let timer: number | undefined
    const tick = async () => {
      const status = await read()
      if (!active) return
      if (isPending(status)) {
        timer = window.setTimeout(tick, POLL_MS)
      } else {
        void refresh()
      }
    }
    void tick()

    return () => {
      active = false
      if (timer) window.clearTimeout(timer)
    }
  }, [selectedId, refresh])

  async function remove(sessionId: string) {
    try {
      await api.deleteSession(sessionId)
      if (sessionId === selectedId) setSelectedId(null)
      await refresh()
    } catch (cause) {
      setError(message(cause))
    }
  }

  return (
    <div className="app">
      <header>
        <h1>Ontology · Data</h1>
        <p className="hint">Upload a dataset and see what it parsed into.</p>
      </header>

      {error && (
        <div className="banner fade-in" role="alert">
          <p>{error}</p>
          <button type="button" className="icon" aria-label="Dismiss" onClick={() => setError(null)}>
            ×
          </button>
        </div>
      )}

      <main>
        <aside>
          <UploadPanel
            onUploaded={(sessionId) => {
              setSelectedId(sessionId)
              void refresh()
            }}
          />
          <SessionList
            sessions={sessions}
            selected={selectedId}
            loading={loading}
            onSelect={setSelectedId}
            onDelete={remove}
          />
        </aside>

        <TableExplorer session={detail} />
      </main>
    </div>
  )
}
