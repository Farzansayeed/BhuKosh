import React, { useEffect, useState } from 'react'
import { api, apiBlob } from './api'

// Document integrity (PS #10 duplicate detection + forged-scan review):
//  - perceptual duplicate check (phash Hamming, instant, no engine cost)
//  - vision forensics (stamps/seals/tamper signals — one Gemini call per page)
//  - identifier library grown from VERIFIED documents ("AI learns the stamps")
// Findings are decision support: a human declares a document forged.
export default function IntegrityPanel({ recordId, onDone }) {
  const [docs, setDocs] = useState(null)
  const [busy, setBusy] = useState(false)
  const [results, setResults] = useState({})
  const [err, setErr] = useState(null)
  const [learned, setLearned] = useState(null)

  useEffect(() => {
    api(`/records/${recordId}/documents`).then(setDocs).catch((e) => setErr(e.message))
  }, [recordId])

  if (!docs || docs.length === 0) return null

  const runCheck = async (docId) => {
    setBusy(true); setErr(null)
    try {
      const r = await api(`/documents/${docId}/integrity-check`, { method: 'POST', body: {} })
      setResults((s) => ({ ...s, [docId]: { ...r, kind: 'check' } }))
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  const runVision = async (docId) => {
    setBusy(true); setErr(null)
    try {
      const r = await api(`/documents/${docId}/integrity-vision`, { method: 'POST', body: {} })
      setResults((s) => ({ ...s, [docId]: { ...r, kind: 'vision' } }))
      if (onDone) onDone()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  const learn = async (docId, pageId, identifiers) => {
    setBusy(true); setErr(null)
    try {
      const r = await api(`/integrity/learn/${pageId}`, {
        method: 'POST', body: { identifiers: identifiers.map((i) => ({ kind: i.kind, label: i.label })) },
      })
      setLearned(`Learned ${r.learned} identifier(s) — library now holds ${r.library_size}.`)
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  const sevChip = (s) => <span className={`chip ${s === 'error' ? 'sev-error' : s === 'warn' ? 'sev-warn' : 'sev-ok'}`}>{s}</span>

  return (
    <div className="card">
      <h2>Document integrity — duplicates & forgery review</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Perceptual check catches re-scans/re-uploads instantly; the vision pass reads stamps, seals and
        alteration signals. It advises — only a human can declare a document forged.
      </p>
      {err && <div className="error-box">{err}</div>}
      {learned && <div className="ok-box">{learned}</div>}

      {docs.map((d) => {
        const res = results[d.id]
        return (
          <div key={d.id} style={{ borderTop: '1px solid var(--border)', padding: '10px 0' }}>
            <div className="row">
              <b>{d.original_filename}</b>
              <span className="muted mono" style={{ fontSize: 11 }}>{d.sha256?.slice(0, 16)}…</span>
              <span className="right" />
              <button className="btn btn-sm" disabled={busy} onClick={() => runCheck(d.id)}>Check duplicates</button>
              <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => runVision(d.id)}>
                Vision forensics (AI)
              </button>
            </div>

            {res?.kind === 'check' && (
              <p style={{ margin: '8px 0 0' }}>
                {sevChip(res.duplicate_findings.length ? 'warn' : 'ok')}{' '}
                {res.pages_checked} page(s) compared against the corpus —{' '}
                {res.duplicate_findings.length === 0
                  ? 'no near-duplicates found.'
                  : res.duplicate_findings.map((f, i) => <div key={i} className="mini-row">⚠ {f.detail}</div>)}
              </p>
            )}

            {res?.kind === 'vision' && res.pages.map((p) => (
              <div key={p.page_id} style={{ margin: '10px 0', padding: 10, background: 'var(--panel-2)', borderRadius: 8 }}>
                <div className="row">
                  <b>Page {p.seq_no}</b>
                  {sevChip(p.verdict === 'CLEAN' ? 'ok' : p.verdict === 'SUSPECT' ? 'warn' : 'error')}
                  <b>{p.verdict}</b>
                </div>
                <p style={{ margin: '6px 0' }}>{p.summary}</p>
                {p.tamper_signals?.length > 0 && (
                  <ul style={{ margin: '4px 0', paddingLeft: 18 }}>
                    {p.tamper_signals.map((t, i) => (
                      <li key={i}><b>{t.signal}</b> ({Math.round((t.confidence || 0) * 100)}%) — {t.location || t.reasoning || 'location unspecified'}</li>
                    ))}
                  </ul>
                )}
                {p.identifiers?.length > 0 && (
                  <>
                    <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>Identifiers seen:</div>
                    {p.identifiers.map((idn, i) => (
                      <span key={i} className="chip" style={{ marginRight: 6 }}>{idn.kind}: {idn.label}</span>
                    ))}
                    <button className="btn btn-sm" style={{ marginLeft: 6 }} disabled={busy}
                      onClick={() => learn(p.page_id ?? d.id, p.page_id ?? d.id, p.identifiers)}
                      title="Teach the library these stamps/seals from this verified document">
                      ↺ Learn these identifiers
                    </button>
                  </>
                )}
              </div>
            ))}
          </div>
        )
      })}
    </div>
  )
}
