import React, { useEffect, useState } from 'react'
import { api, apiBlob } from './api'

// The complete original file(s) a record was read from. Bytes are fetched as
// an authenticated blob (the JWT can't ride on <img src> or <iframe src>),
// rendered inline for images and PDFs.
export default function SourceFiles({ recordId }) {
  const [docs, setDocs] = useState(null)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    api(`/records/${recordId}/documents`).then(setDocs).catch((e) => setError(e.message))
  }, [recordId])

  return (
    <div className="card">
      <h2 style={{ marginBottom: 4 }}>
        Source documents{' '}
        {docs && <span className="muted" style={{ fontWeight: 400 }}>({docs.length})</span>}
      </h2>
      <p className="muted" style={{ marginTop: 0 }}>
        The complete original file(s) every value was read from — content-addressed (SHA-256), served only
        through authenticated API. What the AI saw is exactly what you see.
      </p>
      {error && <div className="error-box">{error}</div>}
      {docs && docs.length === 0 && <p className="muted">No source documents recorded for this record.</p>}
      {(docs || []).map((d) => (
        <SourceDoc key={d.id} doc={d} expanded={open === d.id} onToggle={() => setOpen(open === d.id ? null : d.id)} />
      ))}
    </div>
  )
}

function SourceDoc({ doc, expanded, onToggle }) {
  const [url, setUrl] = useState(null)
  const [error, setError] = useState(null)
  const isImage = (doc.mime || '').startsWith('image/')
  const isPdf = doc.mime === 'application/pdf'

  useEffect(() => {
    if (!expanded || url) return
    let revoked = null
    apiBlob(`/documents/${doc.id}/content`)
      .then((b) => { revoked = URL.createObjectURL(b); setUrl(revoked) })
      .catch((e) => setError(e.message))
    return () => { if (revoked) URL.revokeObjectURL(revoked) }
  }, [expanded, doc.id, url])

  const kb = doc.size_bytes > 1024 ? `${Math.round(doc.size_bytes / 1024)} KB` : `${doc.size_bytes} B`
  return (
    <div style={{ border: '1px solid rgba(128,128,128,0.3)', borderRadius: 6, margin: '8px 0', padding: '8px 10px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <b className="mono">{doc.original_filename || `document-${doc.id}`}</b>
        <span className="muted">{doc.mime} · {kb} · {doc.page_count} page(s)</span>
        <span className="muted mono" style={{ fontSize: 11 }} title="SHA-256 — tamper-evident identity of this exact file">
          sha256 {doc.sha256.slice(0, 12)}…
        </span>
        <span style={{ flex: 1 }} />
        <button className="btn btn-sm" onClick={onToggle}>{expanded ? 'Hide' : 'View file'}</button>
      </div>
      {expanded && (
        <div style={{ marginTop: 8 }}>
          {error && <div className="error-box">{error}</div>}
          {!url && !error && <p className="muted">Loading…</p>}
          {url && isImage && (
            <img src={url} alt={doc.original_filename} style={{ maxWidth: '100%', maxHeight: 520, border: '1px solid rgba(128,128,128,0.4)', borderRadius: 4 }} />
          )}
          {url && isPdf && (
            <object data={url} type="application/pdf" width="100%" height="520" style={{ borderRadius: 4 }}>
              <p>Inline PDF preview unavailable — <a href={url} target="_blank" rel="noreferrer">open the scan</a>.</p>
            </object>
          )}
          {url && !isImage && !isPdf && (
            <p className="muted">Preview not supported for {doc.mime} — <a href={url} target="_blank" rel="noreferrer">download</a>.</p>
          )}
          {url && (
            <p style={{ marginBottom: 0 }}>
              <a href={url} target="_blank" rel="noreferrer">Open full size in new tab ↗</a>
              <span className="muted"> · sha256 {doc.sha256}</span>
            </p>
          )}
        </div>
      )}
    </div>
  )
}
