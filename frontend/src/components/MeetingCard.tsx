import type { MeetingListItem } from '../types'

interface MeetingCardProps {
  meeting: MeetingListItem
  onClick: () => void
}

export default function MeetingCard({ meeting, onClick }: MeetingCardProps) {
  return (
    <div className="meeting-card card" onClick={onClick}>
      <div>
        <div className="title">
          {meeting.title}
          {meeting.degraded && (
            <span
              className="badge badge-yellow"
              style={{ marginLeft: '0.5rem' }}
              title={`${meeting.chunk_failures} of ${meeting.chunk_total} segments failed to summarise`}
            >
              partial
            </span>
          )}
        </div>
        <div className="meta">
          {meeting.date} &middot; {meeting.participants.slice(0, 3).join(', ')}
          {meeting.participants.length > 3 ? '...' : ''}
        </div>
      </div>
      <div className="counts">
        <span>📋 {meeting.action_item_count}</span>
        <span>✅ {meeting.decision_count}</span>
      </div>
    </div>
  )
}
