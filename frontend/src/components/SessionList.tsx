import { useEffect, useRef, useState } from 'react'
import { fullTime, readableSize, relativeTime } from '../format'
import type { SessionSummary } from '../types'

interface Props {
  sessions: SessionSummary[]
  selected: string | null
  loading: boolean
  onSelect: (sessionId: string) => void
  onDelete: (sessionId: string) => void
}

/** Deleting is refused while a session is still being worked on. */
function isFinished(status: string): boolean {
  return status === 'ready' || status === 'failed'
}

export function SessionList({ sessions, selected, loading, onSelect, onDelete }: Props) {
  // Deleting cannot be undone, so the button asks once before it acts. Only one
  // row can be asking at a time, which is why this is an id rather than a flag.
  const [confirming, setConfirming] = useState<string | null>(null)

  // A fresh upload is selected for the user, and the list is long enough to
  // scroll — so the row it selected has to be brought into view as well.
  const selectedRow = useRef<HTMLLIElement>(null)
  useEffect(() => {
    selectedRow.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [selected])

  return (
    <section className="panel">
      <h2>Uploads</h2>

      {loading && sessions.length === 0 && (
        <div className="sessions">
          <div className="skeleton" />
        </div>
      )}

      {!loading && sessions.length === 0 && <p className="hint">Nothing uploaded yet.</p>}

      {sessions.length > 0 && (
        <ul className="sessions">
          {sessions.map((session) => (
            <li
              key={session.session_id}
              ref={session.session_id === selected ? selectedRow : undefined}
              className={session.session_id === selected ? 'session selected' : 'session'}
            >
              <button
                type="button"
                className="session-body"
                onClick={() => onSelect(session.session_id)}
              >
                <span className="session-head">
                  <span className="filenames">{session.filenames.join(', ') || '—'}</span>
                  <span className={`pill ${session.status}`}>{session.status}</span>
                </span>
                <span className="session-meta">
                  <span>{session.format}</span>
                  <span>
                    {session.total_files} file{session.total_files === 1 ? '' : 's'}
                  </span>
                  <span>{readableSize(session.total_size_bytes)}</span>
                  <span title={fullTime(session.created_at)}>
                    {relativeTime(session.created_at)}
                  </span>
                </span>
              </button>

              {isFinished(session.status) &&
                (confirming === session.session_id ? (
                  <span className="confirm">
                    <span>Delete?</span>
                    <button
                      type="button"
                      className="danger"
                      onClick={() => {
                        setConfirming(null)
                        onDelete(session.session_id)
                      }}
                    >
                      Yes
                    </button>
                    <button type="button" onClick={() => setConfirming(null)}>
                      No
                    </button>
                  </span>
                ) : (
                  <button
                    type="button"
                    className="icon session-remove"
                    aria-label={`Delete ${session.filenames.join(', ')}`}
                    onClick={() => setConfirming(session.session_id)}
                  >
                    ×
                  </button>
                ))}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
