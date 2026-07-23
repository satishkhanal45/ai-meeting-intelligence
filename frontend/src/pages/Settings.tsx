import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Config, Stats } from '../types'

export default function Settings() {
  const [config, setConfig] = useState<Config | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)

  useEffect(() => {
    Promise.all([api.getConfig(), api.getStats()])
      .then(([c, s]) => { setConfig(c); setStats(s) })
      .catch(console.error)
  }, [])

  return (
    <div>
      <h2 style={{ marginBottom: '1.5rem' }}>⚙️ Settings</h2>

      <div className="card mb-2">
        <h3 className="mb-2">API Configuration</h3>
        {['gemini', 'groq', 'openrouter'].map((p) => (
          <div key={p} className="mb-1">
            <strong>{p.charAt(0).toUpperCase() + p.slice(1)}</strong>
            : {config?.configured_providers.includes(p) ? <span className="success">✅ Configured</span> : <span className="error">❌ Not configured</span>}
          </div>
        ))}
        <div className="meta mt-2" style={{ fontSize: '0.8rem' }}>
          Default provider: <strong>{config?.default_provider}</strong> &middot;
          Temperature: <strong>{config?.default_temperature}</strong>
        </div>
        <div className="meta" style={{ fontSize: '0.8rem' }}>
          Add or update API keys in the <code>.env</code> file at the project root.
          Restart the app for changes to take effect.
        </div>
      </div>

      <div className="card">
        <h3 className="mb-2">Database</h3>
        <div className="mb-1">Meetings stored: <strong>{stats?.total_meetings ?? 0}</strong></div>
      </div>

      <div className="card mt-2">
        <h3 className="mb-2">About</h3>
        <div><strong>AI Meeting Intelligence Platform</strong> v1.0.0</div>
        <div className="meta">Built with Python 3.11+, FastAPI, React, and API-based LLMs.</div>
        <div className="meta">Knowledge graphs powered by 3d-force-graph.</div>
      </div>
    </div>
  )
}
