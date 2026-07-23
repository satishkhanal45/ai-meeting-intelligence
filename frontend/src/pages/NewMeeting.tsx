import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import MeetingTabs from '../components/MeetingTabs'
import type { Config, Meeting } from '../types'

export default function NewMeeting() {
  const [config, setConfig] = useState<Config | null>(null)
  const [backendOk, setBackendOk] = useState(true)
  const [tab, setTab] = useState<'paste' | 'upload'>('paste')
  const [text, setText] = useState('')
  const [processing, setProcessing] = useState(false)
  const [progress, setProgress] = useState(0)
  const [result, setResult] = useState<Meeting | null>(null)
  const [error, setError] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.getConfig()
      .then(setConfig)
      .catch(() => setBackendOk(false))
  }, [])

  const handleFile = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => setText(reader.result as string)
    reader.readAsText(file)
  }, [])

  const handleProcess = async () => {
    if (!text.trim()) return
    setProcessing(true)
    setProgress(10)
    setError('')
    setResult(null)

    try {
      const meeting = await api.processTranscript({
        text,
        provider_name: '',
        temperature: config?.default_temperature ?? 0.3,
        chunk_size: config?.default_chunk_size ?? 1000,
        chunk_overlap: config?.default_chunk_overlap ?? 200,
      })
      setProgress(100)
      setResult(meeting)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Processing failed')
    } finally {
      setProcessing(false)
    }
  }

  return (
    <div>
      <h2 style={{ marginBottom: '1.5rem' }}>📝 New Meeting</h2>

      {!backendOk && (
        <div className="error" style={{ marginBottom: '1rem' }}>
          Cannot connect to the backend server. Make sure the API server is running on port 8080.
          If using Docker Compose, run: <code>docker-compose up -d</code>
        </div>
      )}

      <div className="radio-group">
        <label>
          <input type="radio" name="mode" checked={tab === 'paste'} onChange={() => setTab('paste')} />
          <span>Paste transcript</span>
        </label>
        <label>
          <input type="radio" name="mode" checked={tab === 'upload'} onChange={() => setTab('upload')} />
          <span>Upload file</span>
        </label>
      </div>

      {tab === 'paste' ? (
        <textarea
          className="textarea"
          placeholder="Paste meeting transcript here..."
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={processing}
        />
      ) : (
        <div>
          <input
            ref={fileRef}
            type="file"
            accept=".txt,.md,.csv,.json,.html"
            onChange={handleFile}
            style={{ marginBottom: '0.5rem' }}
          />
          {text && <div className="success">Loaded {text.length} characters</div>}
        </div>
      )}

      {text && (
        <div className="meta mb-2" style={{ fontSize: '0.8rem' }}>
          Transcript length: {text.length} characters
        </div>
      )}

      <div className="flex gap-1" style={{ alignItems: 'center' }}>
        <button className="btn btn-primary" onClick={handleProcess} disabled={processing || !text.trim() || !backendOk}>
          {processing ? <><span className="spinner" /> Processing...</> : '🚀 Process Meeting'}
        </button>
        {processing && <span className="info" style={{ fontSize: '0.85rem' }}>Processing... this may take a minute.</span>}
      </div>

      {processing && (
        <div className="progress-bar">
          <div className="fill" style={{ width: `${progress}%` }} />
        </div>
      )}

      {error && <div className="error">Processing failed: {error}</div>}

      {result && (
        <>
          <hr />
          <h3 className="mb-2">📄 {result.title}</h3>
          <MeetingTabs meeting={result} />
          <div className="meta mt-2" style={{ fontSize: '0.8rem' }}>
            Processed with {result.provider} &middot; {result.processing_time}s &middot; {result.date}
          </div>
        </>
      )}
    </div>
  )
}
