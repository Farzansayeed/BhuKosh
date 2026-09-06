import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'

export default function UploadPage() {
  const { role } = useAuth()
  const canWrite = ['operator', 'checker', 'certifier', 'admin'].includes(role)

  const [registerRef, setRegisterRef] = useState(`REG-${new Date().toISOString().slice(0, 10)}`)
  const [centre, setCentre] = useState('')
  const [device, setDevice] = useState('')
  const [file, setFile] = useState(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [step, setStep] = useState('')
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const [recordId, setRecordId] = useState(null)

  if (!canWrite) {
    return <><h1>Upload & Extract</h1><div className="error-box">Your role ({role}) cannot upload documents.</div></>
  }

  const run = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setResult(null)
    setRecordId(null)
    try {
      setStep('Creating intake manifest…')
      const m = await api('/intake', {
        method: 'POST',
        body: { register_ref: `${registerRef}-${Date.now()}`, centre: centre || 'UI', device: device || 'UI', expected_count: 1 },
      })

      setStep('Uploading scan…')
      const fd = new FormData()
      fd.append('manifest_id', String(m.id))
      fd.append('file', file)
      const d = await api('/documents', { method: 'POST', formData: fd })

      setStep('Registering page…')
      const p = await api(`/documents/${d.id}/pages`, { method: 'POST', body: { seq_no: 1, sha256: d.sha256 } })

      setStep('Extracting fields (Gemini)…')
      const ex = await api(`/documents/${d.id}/extract`, { method: 'POST', body: { text, page_id: p.id } })

      setResult({ manifest: m, document: d, page: p, run: ex.run, candidates: ex.candidates })
      setStep('')
    } catch (err) {
      setError(`${step ? step + ' ' : ''}failed: ${err.message}`)
      setStep('')
    } finally {
      setBusy(false)
    }
  }

  const project = async () => {
    setBusy(true)
    setError(null)
    try {
      const rec = await api(`/records/from-run/${result.run.id}`, { method: 'POST' })
      setRecordId(rec.record.id)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <h1>Upload & Extract</h1>
      <p className="sub">Intake → custody → extraction → record. Every step is logged and evidence-bound.</p>

      <form onSubmit={run} className="card">
        <h2>1 · Intake manifest</h2>
        <div className="row">
          <div className="field" style={{ flex: 1, minWidth: 180 }}>
            <label>Register reference</label>
            <input value={registerRef} onChange={(e) => setRegisterRef(e.target.value)} required />
          </div>
          <div className="field" style={{ flex: 1, minWidth: 140 }}>
            <label>Centre</label>
            <input value={centre} onChange={(e) => setCentre(e.target.value)} placeholder="e.g. Tehsil Salempr" />
          </div>
          <div className="field" style={{ flex: 1, minWidth: 140 }}>
            <label>Device</label>
            <input value={device} onChange={(e) => setDevice(e.target.value)} placeholder="e.g. Scanner-01" />
          </div>
        </div>

        <h2>2 · Scan + register text</h2>
        <div className="field">
          <label>Page image (PNG/JPEG/PDF, single page)</label>
          <input type="file" accept="image/png,image/jpeg,application/pdf"
                 onChange={(e) => setFile(e.target.files[0] ?? null)} required />
        </div>
        <div className="field">
          <label>Register line to extract (Devanagari or English)</label>
          <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)}
                    placeholder="राम प्रसाद पुत्र श्याम लाल, खसरा २३४, क्षेत्र २-४० बीघा, ग्राम सलेमपुर" required />
        </div>

        <button className="btn btn-primary" disabled={busy || !file || !text.trim()}>
          {busy ? (step || 'Working…') : 'Run custody → extraction pipeline'}
        </button>
      </form>

      {error && <div className="error-box">{error}</div>}

      {result && (
        <div className="card">
          <h2>Extraction run #{result.run.id} — <span className={`badge ${result.run.status}`}>{result.run.status}</span></h2>
          <p className="muted">
            Manifest #{result.manifest.id} → Document #{result.document.id} (sha <span className="mono">{result.document.sha256.slice(0, 16)}…</span>) → Page #{result.page.id}
          </p>
          <div className="detail-grid">
            {result.candidates.map((c) => (
              <div key={c.id} className="value-card">
                <div className="fv-label">{c.field_type.replace(/_/g, ' ')}</div>
                <div className={`fv-value${c.is_unknown ? ' unknown' : ''}`}>{c.value ?? 'UNKNOWN'}</div>
                <div className="muted mono">raw: {c.raw_value ?? '—'}{c.confidence != null ? ` · conf ${c.confidence}` : ''}</div>
              </div>
            ))}
          </div>
          {recordId
            ? <div className="ok-box">Record created — <Link to={`/records/${recordId}`}>open record #{recordId} →</Link></div>
            : <button className="btn btn-primary mt" onClick={project} disabled={busy}>Create land record from this run →</button>}
        </div>
      )}
    </>
  )
}
