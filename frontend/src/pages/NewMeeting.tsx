import { useCallback, useEffect, useRef, useState } from 'react'
import { api, followJob } from '../api/client'
import MeetingTabs from '../components/MeetingTabs'
import type { Config, Job, Meeting, ProvidersResponse } from '../types'

const STAGE_LABELS: Record<string, string> = {
  queued: 'Queued',
  preparing: 'Preparing transcript',
  summarising: 'Summarising segments',
  merging: 'Merging summaries',
  extracting: 'Extracting action items',
  graphing: 'Building knowledge graph',
  saving: 'Saving',
  done: 'Complete',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

export default function NewMeeting() {
  const [config, setConfig] = useState<Config | null>(null)
  const [providers, setProviders] = useState<ProvidersResponse | null>(null)
  const [backendOk, setBackendOk] = useState(true)
  const [tab, setTab] = useState<'paste' | 'upload'>('paste')
  const [text, setText] = useState('')
  const [providerName, setProviderName] = useState('')
  const [model, setModel] = useState('')
  const [job, setJob] = useState<Job | null>(null)
  const [result, setResult] = useState<Meeting | null>(null)
  const [error, setError] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const followerRef = useRef<{ cancel: () => void } | null>(null)

  const processing = job !== null && (job.status === 'queued' || job.status === 'running')

  useEffect(() => {
    Promise.all([api.getConfig(), api.getProviders()])
      .then(([c, p]) => {
        setConfig(c)
        setProviders(p)
        setProviderName(p.default_provider)
      })
      .catch(() => setBackendOk(false))
    return () => followerRef.current?.cancel()
  }, [])

  const selectedProvider = providers?.providers.find((p) => p.name === providerName)

  const handleFile = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => setText(reader.result as string)
    reader.readAsText(file)
  }, [])

  const handleProcess = async () => {
    if (!text.trim()) return
    setError('')
    setResult(null)
    setJob(null)

    try {
      const { job_id } = await api.startProcessing({
        text,
        provider_name: providerName,
        model,
        temperature: config?.default_temperature ?? 0.3,
        chunk_size: config?.default_chunk_size ?? 1000,
        chunk_overlap: config?.default_chunk_overlap ?? 200,
      })

      followerRef.current = followJob(job_id, (update) => {
        setJob(update)
        if (update.status === 'succeeded' && update.meeting_id) {
          api.getMeeting(update.meeting_id).then(setResult).catch(() => {
            setError('Processing finished but the meeting could not be loaded.')
          })
        }
        if (update.status === 'failed') {
          setError(
            update.error_id
              ? `${update.error ?? 'Processing failed'} (error id: ${update.error_id})`
              : update.error ?? 'Processing failed',
          )
        }
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Processing failed')
    }
  }

  const handleCancel = async () => {
    if (!job) return
    try {
      await api.cancelJob(job.job_id)
    } catch {
      /* the job may have finished in the meantime */
    }
    followerRef.current?.cancel()
    setJob({ ...job, status: 'cancelled', stage: 'cancelled' })
  }

  const percent = Math.round((job?.fraction ?? 0) * 100)

  return (
    <div>
      <h2 style={{ marginBottom: '1.5rem' }}>📝 New Meeting</h2>

      {!backendOk && (
        <div className="error" style={{ marginBottom: '1rem' }}>
          Cannot connect to the backend server. Make sure the API server is running on port 8080.
          If using Docker Compose, run: <code>docker compose up -d</code>
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
          {text && <div className="success">Loaded {text.length.toLocaleString()} characters</div>}
        </div>
      )}

      {providers && (
        <div className="flex gap-1 mb-2" style={{ flexWrap: 'wrap', alignItems: 'center' }}>
          <label className="meta" style={{ fontSize: '0.8rem' }}>
            Provider{' '}
            <select
              className="select"
              value={providerName}
              onChange={(e) => {
                setProviderName(e.target.value)
                setModel('')
              }}
              disabled={processing}
            >
              {providers.providers.map((p) => (
                <option key={p.name} value={p.name} disabled={!p.configured}>
                  {p.name}
                  {p.configured ? '' : ' (no API key)'}
                </option>
              ))}
            </select>
          </label>

          {selectedProvider && (
            <label className="meta" style={{ fontSize: '0.8rem' }}>
              Model{' '}
              <select
                className="select"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                disabled={processing}
              >
                <option value="">{selectedProvider.default_model} (default)</option>
                {selectedProvider.available_models
                  .filter((m) => m !== selectedProvider.default_model)
                  .map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
              </select>
            </label>
          )}
        </div>
      )}

      {text && (
        <div className="meta mb-2" style={{ fontSize: '0.8rem' }}>
          Transcript length: {text.length.toLocaleString()} characters
        </div>
      )}

      <div className="flex gap-1" style={{ alignItems: 'center' }}>
        <button
          className="btn btn-primary"
          onClick={handleProcess}
          disabled={processing || !text.trim() || !backendOk}
        >
          {processing ? (
            <>
              <span className="spinner" /> Processing...
            </>
          ) : (
            '🚀 Process Meeting'
          )}
        </button>
        {processing && (
          <button className="btn btn-danger" onClick={handleCancel}>
            Cancel
          </button>
        )}
      </div>

      {job && processing && (
        <>
          <div className="progress-bar">
            <div className="fill" style={{ width: `${percent}%` }} />
          </div>
          <div className="meta" style={{ fontSize: '0.8rem' }}>
            {percent}% &middot; {STAGE_LABELS[job.stage] ?? job.stage}
            {job.total > 1 && ` (${job.completed}/${job.total})`}
            {job.message && ` — ${job.message}`}
          </div>
        </>
      )}

      {error && <div className="error">{error}</div>}

      {result && (
        <>
          <hr />
          <h3 className="mb-2">📄 {result.title}</h3>

          {result.degraded && (
            <div className="warning mb-2">
              ⚠️ Partial result: {result.chunk_failures} of {result.chunk_total} transcript
              segments could not be summarised, so this summary is incomplete. Re-processing
              may succeed if the provider was temporarily unavailable.
            </div>
          )}
          {result.used_fallback && (
            <div className="info mb-2">
              ℹ️ {result.provider} was unavailable; this was produced by{' '}
              {result.served_by.join(', ')}.
            </div>
          )}

          <MeetingTabs meeting={result} />
          <div className="meta mt-2" style={{ fontSize: '0.8rem' }}>
            {result.provider}
            {result.model && ` · ${result.model}`} &middot; {result.processing_time}s &middot;{' '}
            {result.chunk_total} segments
            {result.input_tokens > 0 &&
              ` · ${(result.input_tokens + result.output_tokens).toLocaleString()} tokens`}
          </div>
        </>
      )}
    </div>
  )
}
