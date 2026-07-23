interface StatCardProps {
  value: number | string
  label: string
}

export default function StatCard({ value, label }: StatCardProps) {
  return (
    <div className="card stat-card">
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  )
}
