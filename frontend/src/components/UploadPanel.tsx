import { useRef, useState } from 'react'
import { ApiError, api } from '../api'

const ACCEPTED = '.csv,.json,.sql,.parquet'

interface Props {
  onUploaded: (sessionId: string) => void
}

export function UploadPanel({ onUploaded }: Props) {
  const [busy, setBusy] = useState(false)
  const [sending, setSending] = useState<string[]>([])
  const [over, setOver] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)

  async function send(selection: FileList | File[] | null) {
    const files = selection ? Array.from(selection) : []
    if (files.length === 0) return

    setBusy(true)
    setError(null)
    setSending(files.map((file) => file.name))
    try {
      const result = await api.upload(files)
      onUploaded(result.session_id)
    } catch (cause) {
      // The API's rules — one format per upload, at most five files, 20MB in
      // total, no repeated content — all arrive here as a message to show.
      setError(cause instanceof ApiError ? cause.message : String(cause))
    } finally {
      setBusy(false)
      setSending([])
      if (input.current) input.current.value = ''
    }
  }

  return (
    <section className="panel">
      <h2>Upload</h2>

      <button
        type="button"
        className={over ? 'dropzone over' : 'dropzone'}
        disabled={busy}
        onClick={() => input.current?.click()}
        onDragOver={(event) => {
          // Without this the browser opens the file instead of offering a drop.
          event.preventDefault()
          if (!busy) setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault()
          setOver(false)
          if (!busy) void send(event.dataTransfer.files)
        }}
      >
        <span className="dropzone-title">
          {busy ? 'Uploading…' : 'Drop files here, or click to browse'}
        </span>
        <span className="dropzone-sub">
          Up to 5 files of one format, 20MB in total
          <br />
          CSV, JSON, SQL dump or Parquet
        </span>
      </button>

      <input
        ref={input}
        type="file"
        multiple
        accept={ACCEPTED}
        hidden
        onChange={(event) => void send(event.target.files)}
      />

      {busy && (
        <div className="waiting">
          <span className="spinner" />
          <span>{sending.join(', ')}</span>
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  )
}
