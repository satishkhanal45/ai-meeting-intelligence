import { useState } from 'react'
import type { Meeting } from '../types'

interface MeetingTabsProps {
  meeting: Meeting
}

const TABS = ['Summary', 'Action Items', 'Deadlines', 'Decisions', 'Transcript']

function priorityBadge(p: string) {
  const map: Record<string, string> = { high: 'badge-red', medium: 'badge-yellow', low: 'badge-green' }
  return <span className={`badge ${map[p] || 'badge-green'}`}>{p}</span>
}

export default function MeetingTabs({ meeting }: MeetingTabsProps) {
  const [tab, setTab] = useState(0)

  return (
    <div>
      <div className="tabs">
        {TABS.map((t, i) => (
          <button key={t} className={`tab ${i === tab ? 'active' : ''}`} onClick={() => setTab(i)}>
            {t}
          </button>
        ))}
      </div>

      {tab === 0 && (
        <div className="card p-2">
          <p style={{ whiteSpace: 'pre-wrap', lineHeight: 1.7 }}>{meeting.summary.executive_summary}</p>
        </div>
      )}

      {tab === 1 && (
        <div>
          {meeting.action_items.length === 0 ? (
            <div className="info">No action items extracted.</div>
          ) : (
            meeting.action_items.map((item, i) => (
              <div key={i} className="card mb-1" style={{ padding: '0.75rem 1rem' }}>
                <div className="flex-between">
                  <div>
                    <strong>{item.task}</strong>
                    <div className="meta" style={{ fontSize: '0.8rem' }}>
                      Owner: {item.owner || '—'} &middot; Status: {item.status}
                    </div>
                  </div>
                  {priorityBadge(item.priority)}
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {tab === 2 && (
        <div>
          {meeting.deadlines.length === 0 ? (
            <div className="info">No deadlines extracted.</div>
          ) : (
            meeting.deadlines.map((dl, i) => (
              <div key={i} className="card mb-1" style={{ padding: '0.75rem 1rem' }}>
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
          {meeting.decisions.length === 0 ? (
            <div className="info">No key decisions extracted.</div>
          ) : (
            meeting.decisions.map((dec, i) => (
              <div key={i} className="card mb-1" style={{ padding: '0.75rem 1rem' }}>
                <strong>✅ {dec.decision}</strong>
                {dec.rationale && <div className="meta mt-2" style={{ fontSize: '0.8rem' }}>Rationale: {dec.rationale}</div>}
              </div>
            ))
          )}
        </div>
      )}

      {tab === 4 && (
        <div>
          <div className="card mb-1" style={{ padding: '0.75rem 1rem' }}>
            <details>
              <summary style={{ cursor: 'pointer', fontWeight: 600 }}>Raw transcript ({meeting.transcript.raw_text.length} chars)</summary>
              <pre style={{ marginTop: '0.5rem', whiteSpace: 'pre-wrap', fontSize: '0.85rem', color: 'var(--text-muted)' }}>{meeting.transcript.raw_text}</pre>
            </details>
          </div>
          <div className="card mb-1" style={{ padding: '0.75rem 1rem' }}>
            <details>
              <summary style={{ cursor: 'pointer', fontWeight: 600 }}>Cleaned transcript ({meeting.transcript.cleaned_text.length} chars)</summary>
              <pre style={{ marginTop: '0.5rem', whiteSpace: 'pre-wrap', fontSize: '0.85rem', color: 'var(--text-muted)' }}>{meeting.transcript.cleaned_text}</pre>
            </details>
          </div>
        </div>
      )}
    </div>
  )
}
