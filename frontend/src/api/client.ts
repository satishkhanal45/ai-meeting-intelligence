import type { Config, KnowledgeGraph, Meeting, MeetingListItem, Stats } from '../types'

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
  } catch {
    throw new Error(getErrorMessage(0, ''))
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(getErrorMessage(res.status, body.detail))
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

export const api = {
  getStats: () => request<Stats>('/stats'),

  listMeetings: (search?: string) =>
    request<MeetingListItem[]>(`/meetings${search ? `?search=${encodeURIComponent(search)}` : ''}`),

  getMeeting: (id: string) => request<Meeting>(`/meetings/${id}`),

  deleteMeeting: (id: string) => request<void>(`/meetings/${id}`, { method: 'DELETE' }),

  getGraph: (id: string) => request<KnowledgeGraph>(`/meetings/${id}/graph`),

  processTranscript: (body: {
    text: string
    provider_name?: string
    temperature?: number
    chunk_size?: number
    chunk_overlap?: number
    chunk_mode?: string
  }) =>
    request<Meeting>('/process', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  getConfig: () => request<Config>('/config'),

  checkHealth: () => request<{ status: string }>('/config'),
}
