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
  const navigate = useNavigate()

  useEffect(() => {
    Promise.all([api.getStats(), api.listMeetings()])
      .then(([s, m]) => { setStats(s); setMeetings(m) })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="info">Loading...</div>

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
        meetings.slice(0, 10).map((m) => (
          <MeetingCard key={m.id} meeting={m} onClick={() => navigate(`/history?meeting=${m.id}`)} />
        ))
      )}
    </div>
  )
}
