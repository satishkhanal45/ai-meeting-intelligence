import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { ActionStatus, OwnedActionItem } from '../types'

const COLUMNS: { status: ActionStatus; label: string }[] = [
  { status: 'open', label: 'Open' },
  { status: 'in_progress', label: 'In progress' },
  { status: 'done', label: 'Done' },
]

const PRIORITY_CLASS: Record<string, string> = {
  high: 'badge-red',
  medium: 'badge-yellow',
  low: 'badge-green',
}

/**
 * Action items from every meeting in one place.
 *
 * Items previously existed only inside the meeting that produced them, so
 * "what do I still owe?" meant opening each meeting in turn.
 */
export default function ActionItems() {
  const [items, setItems] = useState<OwnedActionItem[]>([])
  const [owners, setOwners] = useState<string[]>([])
  const [ownerFilter, setOwnerFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const navigate = useNavigate()

  const load = useCallback(() => {
    setLoading(true)
    api
      .listActionItems({ owner: ownerFilter, limit: 500 })
      .then((rows) => {
        setItems(rows)
        if (!ownerFilter) {
          setOwners([...new Set(rows.map((r) => r.owner).filter(Boolean))].sort())
        }
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load action items'))
      .finally(() => setLoading(false))
  }, [ownerFilter])

  useEffect(load, [load])

  const move = async (item: OwnedActionItem, status: ActionStatus) => {
    // Update locally first so the board responds immediately, then reconcile.
    setItems((current) => current.map((i) => (i.id === item.id ? { ...i, status } : i)))
    try {
      await api.updateItem('action-items', item.id, { status })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update item')
      load()
    }
  }

  if (loading) return <div className="info">Loading...</div>

  const counts = COLUMNS.map((c) => items.filter((i) => i.status === c.status).length)

  return (
    <div>
      <div className="flex-between mb-2">
        <h2 style={{ margin: 0 }}>📋 Action Items</h2>
        <a className="btn" href={api.exportActionItemsUrl()} download>
          ⬇️ Export CSV
        </a>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="flex gap-1 mb-2" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
        <label className="meta" style={{ fontSize: '0.8rem' }}>
          Owner{' '}
          <select
            className="select"
            value={ownerFilter}
            onChange={(e) => setOwnerFilter(e.target.value)}
          >
            <option value="">Everyone</option>
            {owners.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </label>
        <span className="meta" style={{ fontSize: '0.8rem' }}>
          {items.length} item{items.length === 1 ? '' : 's'}
        </span>
      </div>

      {items.length === 0 ? (
        <div className="info">No action items yet. Process a meeting to extract some.</div>
      ) : (
        <div className="board">
          {COLUMNS.map((column, index) => (
            <div key={column.status} className="board-column">
              <div className="board-header">
                {column.label} <span className="meta">({counts[index]})</span>
              </div>

              {items
                .filter((item) => item.status === column.status)
                .map((item) => (
                  <div key={item.id} className="card mb-1" style={{ padding: '0.7rem 0.85rem' }}>
                    <div style={{ marginBottom: '0.35rem' }}>{item.task}</div>
                    <div className="flex-between" style={{ alignItems: 'center' }}>
                      <span className="meta" style={{ fontSize: '0.75rem' }}>
                        {item.owner || 'Unassigned'}
                      </span>
                      <span className={`badge ${PRIORITY_CLASS[item.priority] ?? 'badge-green'}`}>
                        {item.priority}
                      </span>
                    </div>
                    <div
                      className="meta"
                      style={{ fontSize: '0.72rem', marginTop: '0.4rem', cursor: 'pointer' }}
                      onClick={() => navigate(`/history?meeting=${item.meeting_id}`)}
                      title="Open the meeting this came from"
                    >
                      ↪ {item.meeting_title}
                    </div>
                    <div className="flex gap-1" style={{ marginTop: '0.5rem' }}>
                      {COLUMNS.filter((c) => c.status !== item.status).map((target) => (
                        <button
                          key={target.status}
                          className="btn btn-small"
                          onClick={() => move(item, target.status)}
                        >
                          → {target.label}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
