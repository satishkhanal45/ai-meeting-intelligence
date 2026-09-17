import { useEffect, useState } from 'react'
import { api } from '../api/client'
import GraphViewer from '../components/GraphViewer'
import type { KnowledgeGraph, MeetingListItem } from '../types'

export default function KnowledgeGraphs() {
  const [meetings, setMeetings] = useState<MeetingListItem[]>([])
  const [selectedId, setSelectedId] = useState<string>('')
  const [graph, setGraph] = useState<KnowledgeGraph | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api
      .listMeetings({ limit: 200 })
      .then(setMeetings)
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load meetings'))
  }, [])

  useEffect(() => {
    if (!selectedId) { setGraph(null); return }
    setLoading(true)
    setError('')
    api.getGraph(selectedId)
      .then(setGraph)
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load graph'))
      .finally(() => setLoading(false))
  }, [selectedId])

  return (
    <div>
      <h2 style={{ marginBottom: '1.5rem' }}>🕸️ Visualization</h2>

      {error && <div className="error">{error}</div>}

      {meetings.length === 0 ? (
        <div className="info">No meetings available. Process a meeting first.</div>
      ) : (
        <>
          <select
            className="select mb-2"
            value={selectedId}
            onChange={(e) => setSelectedId(e.target.value)}
            style={{ maxWidth: 500 }}
          >
            <option value="">Select a meeting to visualise...</option>
            {meetings.map((m) => (
              <option key={m.id} value={m.id}>
                {m.title.slice(0, 60)} ({m.date})
              </option>
            ))}
          </select>

          {loading && <div className="info">Loading graph...</div>}

          {graph && <GraphViewer graph={graph} />}
        </>
      )}
    </div>
  )
}
