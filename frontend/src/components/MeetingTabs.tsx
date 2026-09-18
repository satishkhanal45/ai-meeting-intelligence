import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { ActionItem, ActionPriority, ActionStatus, Meeting } from '../types'

interface MeetingTabsProps {
  meeting: Meeting
  /** Called with the refreshed meeting after an edit, so parents stay in sync. */
  onChange?: (meeting: Meeting) => void
}

const TABS = ['Summary', 'Action Items', 'Deadlines', 'Decisions', 'Transcript']

const STATUSES: ActionStatus[] = ['open', 'in_progress', 'done', 'cancelled']
const PRIORITIES: ActionPriority[] = ['high', 'medium', 'low']

const PRIORITY_CLASS: Record<string, string> = {
  high: 'badge-red',
  medium: 'badge-yellow',
  low: 'badge-green',
}

export default function MeetingTabs({ meeting, onChange }: MeetingTabsProps) {
  const [tab, setTab] = useState(0)
  const [current, setCurrent] = useState<Meeting>(meeting)
  const [busy, setBusy] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [newTask, setNewTask] = useState('')

  useEffect(() => setCurrent(meeting), [meeting])

  const apply = (updated: Meeting) => {
    setCurrent(updated)
    onChange?.(updated)
  }

  const editItem = async (item: ActionItem, patch: Partial<ActionItem>) => {
    setBusy(item.id)
    setError('')
    try {
      apply(await api.updateItem('action-items', item.id, patch))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update item')
    } finally {
      setBusy(null)
    }
  }

  const removeItem = async (item: ActionItem) => {
    setBusy(item.id)
    setError('')
    try {
      await api.deleteItem('action-items', item.id)
      apply({ ...current, action_items: current.action_items.filter((i) => i.id !== item.id) })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete item')
    } finally {
      setBusy(null)
    }
  }

  const addItem = async () => {
    if (!newTask.trim()) return
    setError('')
    try {
      await api.createItem(current.id, 'action-items', { task: newTask.trim() })
      setNewTask('')
      apply(await api.getMeeting(current.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not add item')
    }
  }

  return (
    <div>
      <div className="tabs">
        {TABS.map((t, i) => (
          <button key={t} className={`tab ${i === tab ? 'active' : ''}`} onClick={() => setTab(i)}>
            {t}
          </button>
        ))}
      </div>

      {error && <div className="error">{error}</div>}

      {tab === 0 && (
        <div className="card p-2">
          <p style={{ whiteSpace: 'pre-wrap', lineHeight: 1.7 }}>
            {current.summary.executive_summary}
          </p>
        </div>
      )}

      {tab === 1 && (
        <div>
          {current.action_items.length === 0 ? (
            <div className="info">No action items extracted.</div>
          ) : (
            current.action_items.map((item) => (
              <div
                key={item.id}
                className="card mb-1"
                style={{ padding: '0.75rem 1rem', opacity: busy === item.id ? 0.55 : 1 }}
              >
                <div className="flex-between" style={{ gap: '1rem' }}>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <strong
                      style={{
                        textDecoration: item.status === 'done' ? 'line-through' : undefined,
                      }}
                    >
                      {item.task}
                    </strong>
                    <div
                      className="flex gap-1"
                      style={{ marginTop: '0.4rem', alignItems: 'center', flexWrap: 'wrap' }}
                    >
                      <input
                        className="input input-inline"
                        value={item.owner}
                        placeholder="Unassigned"
                        onChange={(e) =>
                          setCurrent({
                            ...current,
                            action_items: current.action_items.map((i) =>
                              i.id === item.id ? { ...i, owner: e.target.value } : i,
                            ),
                          })
                        }
                        onBlur={(e) => {
                          const owner = e.target.value
                          const original = meeting.action_items.find((i) => i.id === item.id)
                          if (original && original.owner !== owner) editItem(item, { owner })
                        }}
                      />
                      <select
                        className="select select-inline"
                        value={item.status}
                        onChange={(e) => editItem(item, { status: e.target.value as ActionStatus })}
                      >
                        {STATUSES.map((s) => (
                          <option key={s} value={s}>
                            {s.replace('_', ' ')}
                          </option>
                        ))}
                      </select>
                      <select
                        className="select select-inline"
                        value={item.priority}
                        onChange={(e) =>
                          editItem(item, { priority: e.target.value as ActionPriority })
                        }
                      >
                        {PRIORITIES.map((p) => (
                          <option key={p} value={p}>
                            {p}
                          </option>
                        ))}
                      </select>
                      <button
                        className="btn btn-small btn-danger"
                        onClick={() => removeItem(item)}
                        title="Remove this item"
                      >
                        ✕
                      </button>
                    </div>
                  </div>
                  <span className={`badge ${PRIORITY_CLASS[item.priority] ?? 'badge-green'}`}>
                    {item.priority}
                  </span>
                </div>
              </div>
            ))
          )}

          <div className="flex gap-1 mt-2">
            <input
              className="input"
              placeholder="Add an action item the extraction missed..."
              value={newTask}
              onChange={(e) => setNewTask(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addItem()}
            />
            <button className="btn" onClick={addItem} disabled={!newTask.trim()}>
              Add
            </button>
          </div>
        </div>
      )}

      {tab === 2 && (
        <div>
          {current.deadlines.length === 0 ? (
            <div className="info">No deadlines extracted.</div>
          ) : (
            current.deadlines.map((dl) => (
              <div key={dl.id} className="card mb-1" style={{ padding: '0.75rem 1rem' }}>
                <strong>{dl.description}</strong>
                <div className="meta" style={{ fontSize: '0.8rem' }}>
                  {dl.date} &middot; {dl.type}
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {tab === 3 && (
        <div>
          {current.decisions.length === 0 ? (
            <div className="info">No key decisions extracted.</div>
          ) : (
            current.decisions.map((dec) => (
              <div key={dec.id} className="card mb-1" style={{ padding: '0.75rem 1rem' }}>
                <strong>✅ {dec.decision}</strong>
                {dec.rationale && (
                  <div className="meta mt-2" style={{ fontSize: '0.8rem' }}>
                    Rationale: {dec.rationale}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {tab === 4 && (
        <div>
          <div className="card mb-1" style={{ padding: '0.75rem 1rem' }}>
            <details>
              <summary style={{ cursor: 'pointer', fontWeight: 600 }}>
                Raw transcript ({current.transcript.raw_text.length.toLocaleString()} chars)
              </summary>
              <pre
                style={{
                  marginTop: '0.5rem',
                  whiteSpace: 'pre-wrap',
                  fontSize: '0.85rem',
                  color: 'var(--text-muted)',
                }}
              >
                {current.transcript.raw_text}
              </pre>
            </details>
          </div>
          <div className="card mb-1" style={{ padding: '0.75rem 1rem' }}>
            <details>
              <summary style={{ cursor: 'pointer', fontWeight: 600 }}>
                Cleaned transcript ({current.transcript.cleaned_text.length.toLocaleString()} chars)
              </summary>
              <pre
                style={{
                  marginTop: '0.5rem',
                  whiteSpace: 'pre-wrap',
                  fontSize: '0.85rem',
                  color: 'var(--text-muted)',
                }}
              >
                {current.transcript.cleaned_text}
              </pre>
            </details>
          </div>
        </div>
      )}
    </div>
  )
}
