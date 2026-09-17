import { NavLink } from 'react-router-dom'

const NAV = [
  { path: '/', label: 'Dashboard', icon: '📊' },
  { path: '/new', label: 'New Meeting', icon: '📝' },
  { path: '/history', label: 'Meeting History', icon: '📚' },
  { path: '/graph', label: 'Visualization', icon: '🕸️' },
  { path: '/settings', label: 'Settings', icon: '⚙️' },
]

interface LayoutProps {
  children: React.ReactNode
}

export default function Layout({ children }: LayoutProps) {
  return (
    <div className="layout">
      <aside className="sidebar">
        <h1>🧠 Meeting AI</h1>
        <nav>
          {NAV.map(({ path, label, icon }) => (
            <NavLink key={path} to={path} className={({ isActive }) => isActive ? 'active' : ''} end={path === '/'}>
              <span>{icon}</span>
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">AI Meeting Intelligence v1.0</div>
      </aside>
      <main className="main">{children}</main>
    </div>
  )
}
