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

  const fetchList = () => {
    setLoading(true)
    api.listMeetings(search)
      .then(setMeetings)
      .catch(console.error)
      .finally(() => setLoading(false))
  }

  useEffect(() => { fetchList() }, [search])

  useEffect(() => {
    if (!selected) { setMeeting(null); return }
    api.getMeeting(selected).then(setMeeting).catch(console.error)
  }, [selected])

  const handleSelect = (id: string) => {
    setSelected(id)
    setSearchParams({ meeting: id })
  }

  const handleDelete = async (id: string) => {
    await api.deleteMeeting(id)
    setMeeting(null)
    setSelected(null)
    setSearchParams({})
    fetchList()
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
            <div className="flex-between mb-2">
              <h3 style={{ margin: 0 }}>{meeting.title}</h3>
              <button className="btn btn-danger" onClick={() => handleDelete(meeting.id)}>
                🗑️ Delete
              </button>
            </div>
            <MeetingTabs meeting={meeting} />
            <div className="meta mt-2" style={{ fontSize: '0.8rem' }}>
              {meeting.date} &middot; {meeting.participants.join(', ')} &middot; via {meeting.provider}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
