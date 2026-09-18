import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import MeetingCard from '../components/MeetingCard'
import type { PersonDetail, PersonSummary } from '../types'

const PRIORITY_CLASS: Record<string, string> = {
  high: 'badge-red',
  medium: 'badge-yellow',
  low: 'badge-green',
}

/**
 * Who appears across the archive, and what each of them owns.
 *
 * Participants were stored as a JSON blob per meeting, so "everything Alice
 * owns" was not answerable without deserialising every row.
 */
export default function People() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [people, setPeople] = useState<PersonSummary[]>([])
  const [person, setPerson] = useState<PersonDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const selected = searchParams.get('person')
  const navigate = useNavigate()

  useEffect(() => {
    api
      .listPeople()
      .then(setPeople)
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load people'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!selected) {
      setPerson(null)
      return
    }
    api
      .getPerson(selected)
      .then(setPerson)
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load person'))
  }, [selected])

  if (loading) return <div className="info">Loading...</div>

  return (
    <div>
      <h2 style={{ marginBottom: '1.5rem' }}>👥 People</h2>

      {error && <div className="error">{error}</div>}

      {people.length === 0 ? (
        <div className="info">No participants yet. Process a meeting to get started.</div>
      ) : (
        <div style={{ display: 'flex', gap: '2rem', alignItems: 'flex-start' }}>
          <div style={{ flex: selected ? '0 0 320px' : '1' }}>
            {people.map((p) => (
              <div
                key={p.id}
                className="card meeting-card"
                style={{
                  border: selected === p.id ? '1px solid var(--primary)' : undefined,
                }}
                onClick={() => setSearchParams({ person: p.id })}
              >
                <div>
                  <div className="title">{p.name}</div>
                  <div className="meta">
                    {p.meeting_count} meeting{p.meeting_count === 1 ? '' : 's'}
                  </div>
                </div>
                <div className="counts">
                  {p.open_action_item_count > 0 && (
                    <span className="badge badge-yellow">{p.open_action_item_count} open</span>
                  )}
                  <span>📋 {p.action_item_count}</span>
                </div>
              </div>
            ))}
          </div>

          {person && (
            <div style={{ flex: 1, minWidth: 0 }}>
              <h3 className="mb-2">{person.name}</h3>
              <div className="meta mb-2" style={{ fontSize: '0.85rem' }}>
                {person.meetings.length} meeting{person.meetings.length === 1 ? '' : 's'} ·{' '}
                {person.action_items.length} action item
                {person.action_items.length === 1 ? '' : 's'} · {person.open_action_items} still open
              </div>

              <h4 className="mb-1">Action items</h4>
              {person.action_items.length === 0 ? (
                <div className="info">Nothing assigned to this person.</div>
              ) : (
                person.action_items.map((item) => (
                  <div key={item.id} className="card mb-1" style={{ padding: '0.7rem 0.9rem' }}>
                    <div className="flex-between">
                      <div style={{ minWidth: 0 }}>
                        <div
                          style={{
                            textDecoration: item.status === 'done' ? 'line-through' : undefined,
                            opacity: item.status === 'done' ? 0.6 : 1,
                          }}
                        >
                          {item.task}
                        </div>
                        <div
                          className="meta"
                          style={{ fontSize: '0.75rem', cursor: 'pointer' }}
                          onClick={() => navigate(`/history?meeting=${item.meeting_id}`)}
                        >
                          ↪ {item.meeting_title} · {item.status}
                        </div>
                      </div>
                      <span className={`badge ${PRIORITY_CLASS[item.priority] ?? 'badge-green'}`}>
                        {item.priority}
                      </span>
                    </div>
                  </div>
                ))
              )}

              <h4 className="mb-1 mt-2">Meetings</h4>
              {person.meetings.map((m) => (
                <MeetingCard
                  key={m.id}
                  meeting={m}
                  onClick={() => navigate(`/history?meeting=${m.id}`)}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
