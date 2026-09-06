import React, { useEffect, useState } from 'react'
import { api } from './api'
import { useI18n } from './i18n'

// Past vs current (PS: "past data and current data"). Current values are a
// projection of the append-only decision history — this panel shows both:
// every value change (before → after, with reason) and the state timeline.
export default function HistoryPanel({ recordId }) {
  const { t } = useI18n()
  const [h, setH] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    api(`/records/${recordId}/history`).then(setH).catch((e) => setErr(e.message))
  }, [recordId])

  if (err) return <div className="card"><h2>{t('history_title', { n: 0 })}</h2><div className="error-box">{err}</div></div>
  if (!h) return <div className="card"><h2>{t('history_title', { n: 0 })}</h2><p className="muted">{t('loading')}</p></div>

  return (
    <div className="card">
      <h2>{t('history_title', { n: h.decision_count })}</h2>

      {h.changes.length === 0 ? (
        <p className="muted">{t('history_clean')}</p>
      ) : (
        <table>
          <thead><tr><th>{t('col_field')}</th><th>{t('col_past')}</th><th>{t('col_current')}</th><th>{t('col_who')}</th><th>{t('col_reason')}</th><th>{t('col_version')}</th><th>{t('col_when')}</th></tr></thead>
          <tbody>
            {h.changes.map((c, i) => (
              <tr key={i}>
                <td><b>{c.field_type}</b></td>
                <td style={{ color: 'var(--err)' }}>{String(c.before ?? '—')}</td>
                <td style={{ color: 'var(--ok)' }}><b>{String(c.after ?? '—')}</b></td>
                <td>{c.actor} <span className="muted">({c.role})</span></td>
                <td className="muted" style={{ maxWidth: 220 }}>{c.reason || '—'}</td>
                <td className="mono">{c.record_version}</td>
                <td className="muted">{new Date(c.at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {h.timeline.length > 0 && (
        <>
          <h2>{t('state_timeline')}</h2>
          <div>
            {h.timeline.map((t, i) => (
              <div key={i} className="mini-row">
                <span className="badge">{t.from_state}</span>
                <span>→</span>
                <span className="badge">{t.to_state}</span>
                <span className="muted">
                  {t.decision_type} by {t.actor}{t.reason ? ` — "${t.reason}"` : ''}
                </span>
                <span className="muted" style={{ marginLeft: 'auto', fontSize: 12 }}>
                  v{t.record_version} · {new Date(t.at).toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
