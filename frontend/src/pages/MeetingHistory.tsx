import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import MeetingCard from '../components/MeetingCard'
import MeetingTabs from '../components/MeetingTabs'
import type { Meeting, MeetingListItem } from '../types'

export default function MeetingHistory() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [meetings, setMeetings] = useState<MeetingListItem[]>([])
  const [search, setSearch] = useState(searchParams.get('search') || '')
  const [selected, setSelected] = useState<string | null>(searchParams.get('meeting'))
  const [meeting, setMeeting] = useState<Meeting | null>(null)
  const [loading, setLoading] = useState(true)

  const [error, setError] = useState('')
  const [originalTitle, setOriginalTitle] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState(search)
  const [reloadToken, setReloadToken] = useState(0)

  // Debounce so typing does not fire a request per keystroke.
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search), 300)
    return () => window.clearTimeout(timer)
  }, [search])

  useEffect(() => {
    // Abort the in-flight request when the query changes, so a slow earlier
    // response cannot overwrite the results of a later one.
    const controller = new AbortController()
    setLoading(true)
    setError('')
    api
      .listMeetings({ search: debouncedSearch, signal: controller.signal })
      .then(setMeetings)
      .catch((err) => {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setError(err instanceof Error ? err.message : 'Could not load meetings')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [debouncedSearch, reloadToken])

  useEffect(() => {
    if (!selected) { setMeeting(null); return }
    api
      .getMeeting(selected)
      .then((m) => {
        setMeeting(m)
        setOriginalTitle(m.title)
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load meeting'))
  }, [selected])

  const handleSelect = (id: string) => {
    setSelected(id)
    setSearchParams({ meeting: id })
  }

  const handleDelete = async (id: string) => {
    if (!window.confirm('Delete this meeting? This cannot be undone.')) return
    try {
      await api.deleteMeeting(id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete meeting')
      return
    }
    setMeeting(null)
    setSelected(null)
    setSearchParams({})
    setReloadToken((n) => n + 1)
  }

  return (
    <div>
      <h2 style={{ marginBottom: '1.5rem' }}>📚 Meeting History</h2>

      <input
        className="input search-bar"
        placeholder="Search meetings by title, participant, task, decision..."
        value={search}
        onChange={(e) => { setSearch(e.target.value); setSearchParams({ search: e.target.value }) }}
      />

      {error && <div className="error">{error}</div>}

      <div style={{ display: 'flex', gap: '2rem' }}>
        <div style={{ flex: selected ? '0 0 380px' : '1' }}>
          {loading ? (
            <div className="info">Loading...</div>
          ) : meetings.length === 0 ? (
            <div className="info">No meetings found.</div>
          ) : (
            meetings.map((m) => (
              <div key={m.id} style={{ border: selected === m.id ? '1px solid var(--primary)' : 'none', borderRadius: 10, marginBottom: 1 }}>
                <MeetingCard meeting={m} onClick={() => handleSelect(m.id)} />
              </div>
            ))
          )}
        </div>

        {meeting && (
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="flex-between mb-2" style={{ gap: '1rem' }}>
              <input
                className="input title-input"
                value={meeting.title}
                aria-label="Meeting title"
                onChange={(e) => setMeeting({ ...meeting, title: e.target.value })}
                onBlur={async (e) => {
                  const title = e.target.value.trim()
                  if (!title || title === originalTitle) return
                  try {
                    await api.updateMeeting(meeting.id, { title })
                    setOriginalTitle(title)
                    setReloadToken((n) => n + 1)
                  } catch (err) {
                    setError(err instanceof Error ? err.message : 'Could not rename meeting')
                  }
                }}
              />
              <div className="flex gap-1" style={{ whiteSpace: 'nowrap' }}>
                <a className="btn btn-small" href={api.exportMeetingUrl(meeting.id, 'md')} download>
                  ⬇️ MD
                </a>
                <a className="btn btn-small" href={api.exportMeetingUrl(meeting.id, 'csv')} download>
                  CSV
                </a>
                <a className="btn btn-small" href={api.exportMeetingUrl(meeting.id, 'ics')} download>
                  ICS
                </a>
                <button className="btn btn-danger btn-small" onClick={() => handleDelete(meeting.id)}>
                  🗑️
                </button>
              </div>
            </div>
            {meeting.degraded && (
              <div className="warning mb-2">
                ⚠️ Partial result: {meeting.chunk_failures} of {meeting.chunk_total} transcript
                segments could not be summarised, so this summary is incomplete.
              </div>
            )}
            <MeetingTabs meeting={meeting} onChange={setMeeting} />
            <div className="meta mt-2" style={{ fontSize: '0.8rem' }}>
              {meeting.date} &middot; {meeting.participants.join(', ')} &middot; via{' '}
              {meeting.provider}
              {meeting.model && ` (${meeting.model})`}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
