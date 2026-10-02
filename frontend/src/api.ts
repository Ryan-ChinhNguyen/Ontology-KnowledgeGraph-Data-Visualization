import type {
  DatasetTables,
  RowPage,
  SessionDetail,
  SessionList,
  UploadResult,
} from './types'

/** An error the API reported, carrying the message it gave. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init)

  if (!response.ok) {
    // Every 4xx and 5xx from this API carries a `detail`; fall back to the
    // status text for anything that does not, such as a proxy error.
    const detail = await response
      .json()
      .then((body) => body?.detail)
      .catch(() => null)
    throw new ApiError(response.status, detail ?? response.statusText)
  }

  return response.status === 204 ? (undefined as T) : ((await response.json()) as T)
}

export const api = {
  listSessions: (limit = 50) => request<SessionList>(`/sessions?limit=${limit}`),

  getSession: (sessionId: string) => request<SessionDetail>(`/sessions/${sessionId}`),

  deleteSession: (sessionId: string) =>
    request<void>(`/sessions/${sessionId}`, { method: 'DELETE' }),

  getTables: (sessionId: string) => request<DatasetTables>(`/sessions/${sessionId}/tables`),

  getRows: (tableId: string, offset: number, limit: number) =>
    request<RowPage>(`/tables/${tableId}/rows?offset=${offset}&limit=${limit}`),

  upload: (files: File[]) => {
    const form = new FormData()
    files.forEach((file) => form.append('files', file))
    return request<UploadResult>('/upload', { method: 'POST', body: form })
  },
}
