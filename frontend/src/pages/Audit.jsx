import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'
import { useI18n } from '../i18n'

export default function AuditPage() {
  const { role } = useAuth()
  const { t } = useI18n()
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
    return <><h1>{t('audit_title')}</h1><div className="error-box">{t('audit_denied', { role })}</div></>
  }

  const verify = async () => {
    setBusy(true); setError(null)
    try { setVerifyRes(await api('/audit/verify', { method: 'POST' })) }
    catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <>
      <h1>{t('audit_title')}</h1>
      <p className="sub">{t('audit_sub')}</p>

      <div className="row" style={{ marginBottom: 14 }}>
        <button className="btn btn-primary" onClick={verify} disabled={busy}>
          {busy ? t('verifying_chain') : t('verify_full_chain')}
        </button>
        {head && <span className="muted">{t('head')}: <b>{head.seq}</b> · <span className="mono">{String(head.payload_hash).slice(0, 20)}…</span></span>}
      </div>

      {verifyRes && (
        verifyRes.valid
          ? <div className="ok-box">{t('chain_valid', { n: verifyRes.length })}</div>
          : <div className="error-box">{t('chain_invalid', { seq: verifyRes.first_invalid_seq, reason: verifyRes.reason })}</div>
      )}
      {error && <div className="error-box">{error}</div>}

      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr><th>{t('col_seq')}</th><th>{t('col_action')}</th><th>{t('col_actor')}</th><th>{t('col_refs')}</th><th>{t('col_hash')}</th><th>{t('col_at')}</th></tr>
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
            {events && events.length === 0 && <tr><td colSpan={6} className="muted">{t('no_events')}</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  )
}
