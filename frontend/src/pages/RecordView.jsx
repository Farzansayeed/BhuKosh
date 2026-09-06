import React, { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import CropImage from '../CropImage'
import { useAuth } from '../auth'

// Anomaly explanations arrive as structured JSON from the rules engine.
// Render them as plain language: a summary sentence, labeled evidence with
// links, and the raw JSON only as a collapsible last resort. Older anomalies
// (numeric evidence arrays) still fall back to raw JSON.
function AnomalyExplanation({ anomaly }) {
  const ex = anomaly.explanation || {}
  const ev = Array.isArray(ex.evidence) ? ex.evidence : []
  const labeled = ev.length > 0 && typeof ev[0] === 'object'
  return (
    <div style={{ maxWidth: 560 }}>
      {ex.summary
        ? <p style={{ margin: '0 0 6px', whiteSpace: 'pre-line' }}>{ex.summary}</p>
        : !labeled && <p className="mono muted" style={{ margin: 0 }}>{JSON.stringify(ex)}</p>}
      {labeled && (
        <ul style={{ margin: '0 0 6px', paddingLeft: 18 }}>
          {ev.map((e, i) => (
            <li key={i}>
              {e.record_id != null
                ? <Link to={`/records/${e.record_id}`}>record #{e.record_id}</Link>
                : 'this record'}
              {' — '}
              <span className="mono">{e.field}</span>
              {': '}
              {e.value != null ? <b>{String(e.value)}</b> : <i>no value read</i>}
              {e.role ? ` (${e.role})` : ''}
            </li>
          ))}
        </ul>
      )}
      {ex.calculation && (
        <p style={{ margin: '2px 0' }}>
          <span className="chip sev-warn">{ex.calculation}</span>
        </p>
      )}
      {ex.recommended_action && (
        <p className="muted" style={{ margin: '4px 0 0', fontSize: 13 }}>→ {ex.recommended_action}</p>
      )}
      <details style={{ marginTop: 4, fontSize: 12 }}>
        <summary className="muted">technical detail</summary>
        <pre className="mono" style={{ whiteSpace: 'pre-wrap', margin: '4px 0 0' }}>{JSON.stringify(ex, null, 2)}</pre>
      </details>
    </div>
  )
}

// Rule-engine detail dicts read better as "key: value · key: value" than JSON.
function humanDetail(d) {
  if (d == null) return '—'
  if (typeof d !== 'object') return String(d)
  return Object.entries(d).map(([k, v]) => `${k}: ${v}`).join(' · ')
}

function EvidenceDialog({ fieldId, onClose }) {
  const [chain, setChain] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api(`/fields/${fieldId}/replay`).then(setChain).catch((e) => setError(e.message))
  }, [fieldId])

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50,
    }}>
      <div className="card" onClick={(e) => e.stopPropagation()} style={{ width: 560, maxHeight: '80vh', overflow: 'auto' }}>
        <div className="row"><h2 style={{ margin: 0 }}>Evidence chain</h2><button className="btn btn-sm right" onClick={onClose}>Close</button></div>
        {error && <div className="error-box">{error}</div>}
        {!chain && !error && <p className="muted">Loading…</p>}
        {chain && (
          <>
            <h2>Field</h2>
            <dl className="kv">
              <dt>Value shown</dt><dd>{chain.field.current_value ?? <i>UNKNOWN</i>}</dd>
              <dt>Raw (engine)</dt><dd className="mono">{chain.field.raw_value ?? '—'}</dd>
              <dt>State</dt><dd><span className={`badge ${chain.field.state}`}>{chain.field.state}</span></dd>
            </dl>
            <h2>Candidate</h2>
            <dl className="kv">
              <dt>Engine</dt><dd>{chain.candidate.engine}</dd>
              <dt>Confidence</dt><dd>{chain.candidate.confidence ?? '—'}</dd>
              <dt>N-best rank</dt><dd>{chain.candidate.nbest_rank}</dd>
              <dt>is_unknown</dt><dd>{String(chain.candidate.is_unknown)}</dd>
            </dl>
            {chain.candidate.crop_id && (
              <>
                <h2>Evidence crop — the exact pixels this value was read from</h2>
                <CropImage cropId={chain.candidate.crop_id} />
                <p className="muted mono" style={{ fontSize: 12 }}>
                  bbox (px): [{(chain.candidate.crop_bbox || []).join(', ')}]
                </p>
              </>
            )}
            <h2>Extraction run</h2>
            <dl className="kv">
              <dt>Run</dt><dd>#{chain.run.id} ({chain.run.kind}, {chain.run.status})</dd>
              <dt>Engine</dt><dd>{chain.run.engine_name} {chain.run.engine_version ?? ''}</dd>
              <dt>Prompt</dt><dd>{chain.run.prompt_id} v{chain.run.prompt_version}</dd>
              <dt>Input hash</dt><dd className="mono">{chain.run.input_hash}</dd>
              <dt>Raw output</dt><dd className="mono" style={{ wordBreak: 'break-all' }}>{chain.run.raw_output_uri}</dd>
            </dl>
            <h2>Document & page</h2>
            <dl className="kv">
              <dt>Filename</dt><dd>{chain.document?.original_filename ?? '—'}</dd>
              <dt>SHA-256</dt><dd className="mono">{chain.document?.sha256 ?? '—'}</dd>
              <dt>Page</dt><dd>seq {chain.page?.seq_no} (#{chain.page?.id})</dd>
              <dt>Content</dt>
              <dd>
                {chain.document && <a href={`/api${chain.document.content_uri}`} target="_blank" rel="noreferrer">open source scan ↗</a>}
              </dd>
            </dl>
          </>
        )}
      </div>
    </div>
  )
}

function StateActions({ record, onDone }) {
  const { role } = useAuth()
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const decideRoles = ['checker', 'certifier', 'admin'].includes(role)
  const certifyRoles = ['certifier', 'admin'].includes(role)
  const ver = record.record_version
  const state = record.current_state

  const act = async (decision_type, extra = {}) => {
    setBusy(true)
    setError(null)
    try {
      await api(`/records/${record.id}/decisions`, {
        method: 'POST',
        body: { decision_type, expected_version: ver, reason: reason || undefined, ...extra },
      })
      setReason('')
      onDone()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const claim = async () => {
    setBusy(true); setError(null)
    try { await api(`/records/${record.id}/claim`, { method: 'POST' }); onDone() }
    catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const release = async () => {
    setBusy(true); setError(null)
    try { await api(`/records/${record.id}/release`, { method: 'POST' }); onDone() }
    catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="card">
      <h2>Actions</h2>
      {!decideRoles && <p className="muted">Your role ({role}) is read-only on decisions.</p>}
      {decideRoles && (
        <>
          <div className="field">
            <label>Reason (mandatory for REJECT, REOPEN, corrections, anomaly resolution)</label>
            <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
          <div className="row">
            <button className="btn btn-sm" onClick={claim} disabled={busy}>Claim (15 min)</button>
            <button className="btn btn-sm" onClick={release} disabled={busy}>Release</button>
            {state === 'REVIEW_REQUIRED' && (
              <button className="btn btn-ok" onClick={() => act('APPROVE')} disabled={busy}>Approve → Verified</button>
            )}
            {['EXTRACTED', 'VALIDATED', 'REVIEW_REQUIRED'].includes(state) && (
              <button className="btn btn-danger" onClick={() => act('REJECT')} disabled={busy || !reason}>Reject</button>
            )}
            {state === 'VERIFIED' && certifyRoles && (
              <button className="btn btn-primary" onClick={() => act('CERTIFY')} disabled={busy}>Certify</button>
            )}
            {['VERIFIED', 'OFFICER_CERTIFIED', 'REJECTED'].includes(state) && (
              <button className="btn" onClick={() => act('REOPEN')} disabled={busy || !reason}>Reopen</button>
            )}
          </div>
        </>
      )}
      {error && <div className="error-box">{error}</div>}
    </div>
  )
}

function FieldEditor({ field, record, onDone }) {
  const { role } = useAuth()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const canCorrect = ['checker', 'certifier', 'admin'].includes(role)
    && ['EXTRACTED', 'VALIDATED', 'REVIEW_REQUIRED'].includes(record.current_state)

  const submit = async () => {
    setBusy(true); setError(null)
    try {
      await api(`/records/${record.id}/decisions`, {
        method: 'POST',
        body: {
          decision_type: 'CORRECTION',
          expected_version: record.record_version,
          field_id: field.id,
          after_value: value,
          reason: 'UI correction',
        },
      })
      setEditing(false)
      onDone()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (!editing) {
    return <button className="btn btn-sm" onClick={() => { setValue(field.current_value ?? ''); setEditing(true) }} disabled={!canCorrect}>Correct</button>
  }
  return (
    <div style={{ minWidth: 200 }}>
      <input value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
      {error && <div className="error-box" style={{ padding: 6 }}>{error}</div>}
      <div className="row mt" style={{ gap: 6 }}>
        <button className="btn btn-sm btn-primary" onClick={submit} disabled={busy || !value.trim()}>Save</button>
        <button className="btn btn-sm" onClick={() => setEditing(false)} disabled={busy}>Cancel</button>
      </div>
    </div>
  )
}

export default function RecordViewPage() {
  const { id } = useParams()
  const [bundle, setBundle] = useState(null)
  const [validation, setValidation] = useState(null)
  const [error, setError] = useState(null)
  const [evidenceFor, setEvidenceFor] = useState(null)
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  const { role } = useAuth()

  const load = useCallback(() => {
    api(`/records/${id}`).then(setBundle).catch((e) => setError(e.message))
    api(`/records/${id}/validation`).then(setValidation).catch(() => setValidation(null))
  }, [id])
  useEffect(() => { load() }, [load])

  if (error) return <><h1>Record #{id}</h1><div className="error-box">{error}</div></>
  if (!bundle) return <p className="muted">Loading…</p>

  const { record, fields, decisions } = bundle
  const canValidate = ['operator', 'checker', 'certifier', 'admin'].includes(role)
    && ['EXTRACTED', 'VALIDATED'].includes(record.current_state)
  const canExport = ['operator', 'checker', 'certifier', 'admin'].includes(role)

  const runValidation = async () => {
    setBusy(true); setMsg(null); setError(null)
    try {
      const res = await api(`/records/${record.id}/validate`, { method: 'POST' })
      setValidation({ results: res.results, anomalies: res.anomalies })
      setMsg('Validation complete.')
      load()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const createExport = async (format) => {
    setBusy(true); setMsg(null); setError(null)
    try {
      const exp = await api(`/records/${record.id}/exports`, { method: 'POST', body: { format } })
      // authenticated download: fetch with token, then hand the bytes to the browser
      const auth = JSON.parse(localStorage.getItem('bhukosh.auth'))
      const resp = await fetch(`/api/exports/${exp.id}/download`, {
        headers: { Authorization: `Bearer ${auth.access_token}` },
      })
      if (!resp.ok) throw new Error(`download failed (${resp.status})`)
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `bhukosh-record-${record.id}.${format}`
      a.click()
      URL.revokeObjectURL(url)
      setMsg(`Export #${exp.id} created and downloaded (${format}).`)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const openAnomalies = (validation?.anomalies || []).filter((a) => a.status === 'OPEN')

  return (
    <>
      <div className="row">
        <h1>Record #{record.id}</h1>
        <span className={`badge ${record.current_state}`} style={{ fontSize: 13 }}>{record.current_state.replace('_', ' ')}</span>
        <span className="muted">v{record.record_version}</span>
      </div>
      <p className="sub">
        {record.village_code} · khasra <span className="mono">{record.khasra_no}</span>
        {record.claim_owner && <> · claimed by <b>{record.claim_owner}</b></>}
      </p>

      {msg && <div className="ok-box">{msg}</div>}
      {error && <div className="error-box">{error}</div>}

      <h2>Fields — every value one click from its evidence</h2>
      <div className="detail-grid">
        {fields.map((f) => (
          <div key={f.id} className="value-card">
            <div className="fv-label">{f.field_type.replace(/_/g, ' ')} <span className={`badge ${f.state}`} style={{ float: 'right' }}>{f.state}</span></div>
            <div className={`fv-value${f.current_value == null ? ' unknown' : ''}`}>
              {f.current_value ?? 'UNKNOWN'}
            </div>
            <div className="row" style={{ gap: 6 }}>
              <button className="btn btn-sm" onClick={() => setEvidenceFor(f.id)}>Evidence</button>
              <FieldEditor field={f} record={record} onDone={load} />
            </div>
          </div>
        ))}
      </div>

      <StateActions record={record} onDone={load} />

      <div className="card">
        <h2>Validation</h2>
        <div className="row">
          <button className="btn" onClick={runValidation} disabled={busy || !canValidate}>
            {canValidate ? 'Run validation' : 'Run validation (state not editable)'}
          </button>
          {canExport && <>
            <button className="btn" onClick={() => createExport('json')} disabled={busy}>Export JSON</button>
            <button className="btn" onClick={() => createExport('csv')} disabled={busy}>Export CSV</button>
          </>}
        </div>
        {validation && (
          <>
            <table className="mt">
              <thead><tr><th>Rule</th><th>v</th><th>Severity</th><th>Outcome</th><th>Detail</th></tr></thead>
              <tbody>
                {validation.results.map((r) => (
                  <tr key={r.id}>
                    <td className="mono">{r.rule_id}</td>
                    <td>{r.rule_version}</td>
                    <td>{r.severity}</td>
                    <td><span className={`badge ${r.outcome === 'pass' ? 'SUCCEEDED' : 'MISMATCH'}`}>{r.outcome}</span></td>
                    <td className="mono muted" style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis' }}>{humanDetail(r.detail)}</td>
                  </tr>
                ))}
                {validation.results.length === 0 && <tr><td colSpan={5} className="muted">No results yet — run validation.</td></tr>}
              </tbody>
            </table>
            {validation.anomalies.length > 0 && (
              <>
                <h2>Anomalies</h2>
                <table>
                  <thead><tr><th>Rule</th><th>Severity</th><th>Status</th><th>Explanation</th></tr></thead>
                  <tbody>
                    {validation.anomalies.map((a) => (
                      <tr key={a.id}>
                        <td className="mono">{a.rule_id}</td>
                        <td>{a.severity}</td>
                        <td><span className={`badge ${a.status}`}>{a.status}</span></td>
                        <td><AnomalyExplanation anomaly={a} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
            {openAnomalies.length > 0 && <p className="muted mt">Open anomalies hold this record at REVIEW_REQUIRED — resolve them via decisions (reason required).</p>}
          </>
        )}
      </div>

      <div className="card">
        <h2>Decision trail ({decisions.length})</h2>
        {decisions.length === 0 && <p className="muted">No human decisions yet.</p>}
        {decisions.length > 0 && (
          <table>
            <thead><tr><th>#</th><th>Type</th><th>Actor</th><th>Role</th><th>Reason</th><th>Ver</th><th>At</th></tr></thead>
            <tbody>
              {decisions.map((d) => (
                <tr key={d.id}>
                  <td>{d.id}</td>
                  <td><b>{d.decision_type}</b></td>
                  <td>{d.actor_id}</td>
                  <td className="muted">{d.actor_role}</td>
                  <td className="muted" style={{ maxWidth: 260 }}>{d.reason || '—'}</td>
                  <td className="mono">{d.record_version}</td>
                  <td className="muted">{new Date(d.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {evidenceFor && <EvidenceDialog fieldId={evidenceFor} onClose={() => setEvidenceFor(null)} />}
    </>
  )
}
