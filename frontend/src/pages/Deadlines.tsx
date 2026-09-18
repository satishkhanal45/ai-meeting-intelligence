import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { DatedDeadline } from '../types'

/** Parses the ISO prefix of a deadline date, or null for free text. */
function parseDate(value: string): Date | null {
  const match = /(\d{4})-(\d{2})-(\d{2})/.exec(value ?? '')
  if (!match) return null
  const parsed = new Date(`${match[1]}-${match[2]}-${match[3]}T00:00:00`)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function startOfToday(): Date {
  const now = new Date()
  return new Date(now.getFullYear(), now.getMonth(), now.getDate())
}

/**
 * Every deadline across every meeting, on one timeline.
 *
 * Deadlines were only visible inside the meeting that produced them, so
 * nothing showed what was due next, or what had already slipped.
 */
export default function Deadlines() {
  const [deadlines, setDeadlines] = useState<DatedDeadline[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const navigate = useNavigate()

  useEffect(() => {
    api
      .listDeadlines(500)
      .then(setDeadlines)
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load deadlines'))
      .finally(() => setLoading(false))
  }, [])

  const { overdue, upcoming, undated } = useMemo(() => {
    const today = startOfToday()
    const dated = deadlines
      .map((d) => ({ deadline: d, on: parseDate(d.date) }))
      .filter((entry): entry is { deadline: DatedDeadline; on: Date } => entry.on !== null)
      .sort((a, b) => a.on.getTime() - b.on.getTime())

    return {
      overdue: dated.filter((e) => e.on < today),
      upcoming: dated.filter((e) => e.on >= today),
      // Deadlines the model expressed as free text ("next Thursday") cannot be
      // placed on a timeline, but should not vanish either.
      undated: deadlines.filter((d) => parseDate(d.date) === null),
    }
  }, [deadlines])

  if (loading) return <div className="info">Loading...</div>

  const row = (deadline: DatedDeadline, on: Date | null, tone?: string) => (
    <div key={deadline.id} className="card mb-1" style={{ padding: '0.7rem 0.9rem' }}>
      <div className="flex-between">
        <div style={{ minWidth: 0 }}>
          <div>{deadline.description}</div>
          <div
            className="meta"
            style={{ fontSize: '0.75rem', cursor: 'pointer' }}
            onClick={() => navigate(`/history?meeting=${deadline.meeting_id}`)}
          >
            ↪ {deadline.meeting_title}
          </div>
        </div>
        <div style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
          <div className={tone}>
            {on ? on.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : deadline.date}
          </div>
          <span className="meta" style={{ fontSize: '0.72rem' }}>
            {deadline.type}
          </span>
        </div>
      </div>
    </div>
  )

  return (
    <div>
      <div className="flex-between mb-2">
        <h2 style={{ margin: 0 }}>📅 Deadlines</h2>
        <a className="btn" href={api.exportDeadlinesUrl()} download>
          ⬇️ Calendar (.ics)
        </a>
      </div>

      {error && <div className="error">{error}</div>}

      {deadlines.length === 0 ? (
        <div className="info">No deadlines extracted yet.</div>
      ) : (
        <>
          {overdue.length > 0 && (
            <>
              <h4 className="mb-1" style={{ color: 'var(--danger)' }}>
                Overdue ({overdue.length})
              </h4>
              {overdue.map((e) => row(e.deadline, e.on, 'error'))}
            </>
          )}

          <h4 className="mb-1 mt-2">Upcoming ({upcoming.length})</h4>
          {upcoming.length === 0 ? (
            <div className="info">Nothing scheduled ahead.</div>
          ) : (
            upcoming.map((e) => row(e.deadline, e.on))
          )}

          {undated.length > 0 && (
            <>
              <h4 className="mb-1 mt-2">No fixed date ({undated.length})</h4>
              <div className="meta mb-1" style={{ fontSize: '0.78rem' }}>
                Expressed as free text, so they cannot be placed on the timeline or exported to a
                calendar.
              </div>
              {undated.map((d) => row(d, null))}
            </>
          )}
        </>
      )}
    </div>
  )
}
