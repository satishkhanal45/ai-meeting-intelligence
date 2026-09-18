import type {
  ActionItem,
  Config,
  DatedDeadline,
  Health,
  ItemKind,
  Job,
  KnowledgeGraph,
  Meeting,
  MeetingListItem,
  OwnedActionItem,
  PersonDetail,
  PersonSummary,
  ProvidersResponse,
  Stats,
} from '../types'

const BASE = '/api'

function getErrorMessage(status: number, detail: string): string {
  if (status === 0 || status === 502 || status === 504) {
    return 'Backend server is not running. Please start the API server on port 8080.'
  }
  if (status === 500 && detail === 'Internal Server Error') {
    return 'Backend server encountered an error. Please check the API server logs.'
  }
  return detail || `Request failed: ${status}`
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    })
  } catch (err) {
    // An aborted request is a deliberate cancellation, not a backend failure.
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new Error(getErrorMessage(0, ''))
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    const detail = Array.isArray(body.detail)
      ? body.detail.map((d: { msg?: string }) => d.msg ?? '').join('; ')
      : body.detail
    throw new Error(getErrorMessage(res.status, detail))
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

export interface ListOptions {
  search?: string
  limit?: number
  offset?: number
  signal?: AbortSignal
}

export const api = {
  getStats: () => request<Stats>('/stats'),

  listMeetings: ({ search, limit = 50, offset = 0, signal }: ListOptions = {}) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    if (search) params.set('search', search)
    return request<MeetingListItem[]>(`/meetings?${params}`, { signal })
  },

  getMeeting: (id: string) => request<Meeting>(`/meetings/${id}`),

  deleteMeeting: (id: string) => request<void>(`/meetings/${id}`, { method: 'DELETE' }),

  getGraph: (id: string) => request<KnowledgeGraph>(`/meetings/${id}/graph`),

  /** Enqueues processing and returns immediately with a job to follow. */
  startProcessing: (body: {
    text: string
    provider_name?: string
    model?: string
    temperature?: number
    chunk_size?: number
    chunk_overlap?: number
    chunk_mode?: string
  }) =>
    request<{ job_id: string; status: string }>('/process', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  getJob: (jobId: string) => request<Job>(`/jobs/${jobId}`),

  cancelJob: (jobId: string) => request<void>(`/jobs/${jobId}`, { method: 'DELETE' }),

  // ── Cross-meeting views ──
  listPeople: () => request<PersonSummary[]>('/people'),

  getPerson: (id: string) => request<PersonDetail>(`/people/${id}`),

  listActionItems: (opts: { status?: string; owner?: string; limit?: number } = {}) => {
    const params = new URLSearchParams()
    if (opts.status) params.set('status', opts.status)
    if (opts.owner) params.set('owner', opts.owner)
    params.set('limit', String(opts.limit ?? 200))
    return request<OwnedActionItem[]>(`/action-items?${params}`)
  },

  listDeadlines: (limit = 200) => request<DatedDeadline[]>(`/deadlines?limit=${limit}`),

  // ── Editing extracted items ──
  updateMeeting: (id: string, body: { title?: string }) =>
    request<unknown>(`/meetings/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),

  updateItem: (kind: ItemKind, itemId: number, body: Partial<ActionItem> | Record<string, string>) =>
    request<Meeting>(`/items/${kind}/${itemId}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  createItem: (meetingId: string, kind: ItemKind, body: Record<string, string>) =>
    request<{ id: number }>(`/meetings/${meetingId}/${kind}`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  deleteItem: (kind: ItemKind, itemId: number) =>
    request<void>(`/items/${kind}/${itemId}`, { method: 'DELETE' }),

  /** Export URLs are plain links so the browser handles the download. */
  exportMeetingUrl: (id: string, format: 'md' | 'csv' | 'ics' | 'json') =>
    `${BASE}/meetings/${id}/export?format=${format}`,

  exportActionItemsUrl: () => `${BASE}/export/action-items`,

  exportDeadlinesUrl: () => `${BASE}/export/deadlines.ics`,

  getConfig: () => request<Config>('/config'),

  getProviders: () => request<ProvidersResponse>('/providers'),

  checkHealth: () => request<Health>('/health'),
}

/**
 * Follows a job to completion.
 *
 * Prefers the SSE stream, which pushes an update the moment the pipeline
 * reports one. Falls back to polling if EventSource is unavailable or the
 * stream errors, so progress still moves behind a proxy that buffers it.
 */
export function followJob(
  jobId: string,
  onUpdate: (job: Job) => void,
): { cancel: () => void } {
  let closed = false
  let source: EventSource | null = null
  let pollTimer: number | undefined

  const finish = (job: Job) => {
    onUpdate(job)
    if (job.status === 'succeeded' || job.status === 'failed' || job.status === 'cancelled') {
      cancel()
    }
  }

  const poll = async () => {
    if (closed) return
    try {
      const job = await api.getJob(jobId)
      finish(job)
    } catch {
      // Keep polling: a transient failure should not abandon the job.
    }
    if (!closed) pollTimer = window.setTimeout(poll, 1000)
  }

  const cancel = () => {
    closed = true
    source?.close()
    source = null
    if (pollTimer) window.clearTimeout(pollTimer)
  }

  if (typeof EventSource !== 'undefined') {
    source = new EventSource(`${BASE}/jobs/${jobId}/events`)
    source.onmessage = (event) => {
      try {
        finish(JSON.parse(event.data) as Job)
      } catch {
        /* ignore malformed frame */
      }
    }
    source.onerror = () => {
      source?.close()
      source = null
      if (!closed) poll()
    }
  } else {
    poll()
  }

  return { cancel }
}
