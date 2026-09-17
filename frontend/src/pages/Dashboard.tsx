import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import StatCard from '../components/StatCard'
import MeetingCard from '../components/MeetingCard'
import type { MeetingListItem, Stats } from '../types'

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [meetings, setMeetings] = useState<MeetingListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const navigate = useNavigate()

  useEffect(() => {
    Promise.all([api.getStats(), api.listMeetings({ limit: 10 })])
      .then(([s, m]) => { setStats(s); setMeetings(m) })
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load dashboard'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="info">Loading...</div>
  if (error) return <div className="error">{error}</div>

  return (
    <div>
      <h2 style={{ marginBottom: '1.5rem' }}>📊 Dashboard</h2>

      <div className="card-row">
        <StatCard value={stats?.total_meetings ?? 0} label="Total Meetings" />
        <StatCard value={stats?.unique_participants ?? 0} label="Unique Participants" />
        <StatCard value={stats?.total_action_items ?? 0} label="Total Action Items" />
        <StatCard value={stats?.total_decisions ?? 0} label="Total Decisions" />
      </div>

      <hr />

      <h3 className="mb-2">Recent Meetings</h3>

      {meetings.length === 0 ? (
        <div className="info">No meetings processed yet. Go to <strong>New Meeting</strong> to get started.</div>
      ) : (
        meetings.map((m) => (
          <MeetingCard key={m.id} meeting={m} onClick={() => navigate(`/history?meeting=${m.id}`)} />
        ))
      )}
    </div>
  )
}
