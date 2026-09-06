import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'

export default function AuditPage() {
  const { role } = useAuth()
  const [events, setEvents] = useState(null)
  const [head, setHead] = useState(null)
  const [verifyRes, setVerifyRes] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const isAuditor = role === 'admin' || role === 'auditor'

  const load = () => {
    api('/audit/events?limit=200').then(setEvents).catch((e) => setError(e.message))
    api('/audit/chain-head').then(setHead).catch(() => {})
  }
  useEffect(() => { if (isAuditor) load() }, [isAuditor])

  if (!isAuditor) {
    return <><h1>Audit Chain</h1><div className="error-box">Audit access is admin/auditor only (your role: {role}).</div></>
  }

  const verify = async () => {
    setBusy(true); setError(null)
    try { setVerifyRes(await api('/audit/verify', { method: 'POST' })) }
    catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <>
      <h1>Audit Chain</h1>
      <p className="sub">Hash-linked, append-only. Every decision commits an event in the same transaction — tampering breaks the chain and is detectable.</p>

      <div className="row" style={{ marginBottom: 14 }}>
        <button className="btn btn-primary" onClick={verify} disabled={busy}>
          {busy ? 'Verifying full chain…' : 'Verify full chain'}
        </button>
        {head && <span className="muted">head: seq <b>{head.seq}</b> · <span className="mono">{String(head.payload_hash).slice(0, 20)}…</span></span>}
      </div>

      {verifyRes && (
        verifyRes.valid
          ? <div className="ok-box">Chain VALID — {verifyRes.length} events verified, no forks, no broken links, no altered payloads.</div>
          : <div className="error-box">Chain INVALID at seq {verifyRes.first_invalid_seq}: {verifyRes.reason}</div>
      )}
      {error && <div className="error-box">{error}</div>}

      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr><th>Seq</th><th>Action</th><th>Actor</th><th>Refs</th><th>Hash</th><th>At</th></tr>
          </thead>
          <tbody>
            {(events || []).map((ev) => (
              <tr key={ev.seq}>
                <td className="mono">{ev.seq}</td>
                <td><b>{ev.action}</b></td>
                <td>{ev.actor}</td>
                <td className="mono muted" style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {JSON.stringify(ev.entity_refs)}
                </td>
                <td className="mono muted">{String(ev.payload_hash).slice(0, 12)}…</td>
                <td className="muted">{new Date(ev.created_at).toLocaleString()}</td>
              </tr>
            ))}
            {events && events.length === 0 && <tr><td colSpan={6} className="muted">No events yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  )
}
