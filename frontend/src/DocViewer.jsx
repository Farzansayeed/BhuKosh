import React, { useEffect, useMemo, useState } from 'react'
import { api, apiBlob } from './api'

// The source scan, pinned top-right of the record page, expandable to
// fullscreen. Every field the VISION engine read is drawn as a box at the
// exact pixels it came from (crop bbox = absolute page px → percent overlay,
// so it scales with any render size). Text-path records have no pixel
// locations — the panel says so honestly rather than pretending.
export default function DocViewer({ fields, activeFieldId, onSelectField }) {
  const [docs, setDocs] = useState(null)
  const [docId, setDocId] = useState(null)
  const [url, setUrl] = useState(null)
  const [error, setError] = useState(null)
  const [expanded, setExpanded] = useState(false)
  const [dims, setDims] = useState(null) // [naturalWidth, naturalHeight] once loaded

  useEffect(() => {
    if (!expanded) return
    const onKey = (e) => { if (e.key === 'Escape') setExpanded(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [expanded])

  useEffect(() => {
    let cancelled = false
    const rid = fields?.find((f) => f.record_id)?.record_id
    if (!rid) return
    api(`/records/${rid}/documents`)
      .then((ds) => {
        if (cancelled) return
        setDocs(ds)
        // keep the user's doc choice across bundle refreshes (decisions, etc.)
        setDocId((cur) => {
          if (cur && ds.some((d) => d.id === cur)) return cur
          const firstImg = ds.find((d) => (d.mime || '').startsWith('image/'))
          return (firstImg || ds[0])?.id ?? null
        })
      })
      .catch((e) => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true }
  }, [fields])

  useEffect(() => {
    if (!docId) return
    let cancelled = false
    let objectUrl = null
    setUrl(null); setError(null); setDims(null)
    apiBlob(`/documents/${docId}/content`)
      .then((b) => { if (!cancelled) { objectUrl = URL.createObjectURL(b); setUrl(objectUrl) } })
      .catch((e) => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [docId])

  const doc = useMemo(() => (docs || []).find((d) => d.id === docId), [docs, docId])
  const isImage = doc && (doc.mime || '').startsWith('image/')
  const isPdf = doc && doc.mime === 'application/pdf'

  // Boxes only for fields whose evidence came from THIS document. The stored
  // bbox is ABSOLUTE PAGE PIXELS ([x1,y1,x2,y2]) — scale it to a percent
  // overlay against the image's natural size so it lands correctly at any
  // render width.
  const boxes = useMemo(() => {
    if (!dims) return []
    const [W, H] = dims
    return (fields || [])
      .filter((f) => f.pixel_evidence && f.pixel_evidence.doc_id === docId && f.pixel_evidence.bbox_px)
      .map((f) => {
        const [x1, y1, x2, y2] = f.pixel_evidence.bbox_px
        return {
          field: f,
          style: {
            left: `${(x1 / W) * 100}%`,
            top: `${(y1 / H) * 100}%`,
            width: `${((x2 - x1) / W) * 100}%`,
            height: `${((y2 - y1) / H) * 100}%`,
          },
        }
      })
  }, [fields, docId, dims])

  const located = new Set(boxes.map((b) => b.field.id)).size
  const visionFields = (fields || []).filter((f) => f.pixel_evidence && f.pixel_evidence.doc_id).length

  const body = (
    <>
      {error && <div className="error-box">{error}</div>}
      {docs && docs.length === 0 && <p className="muted">No source document recorded for this record.</p>}
      {docs && docs.length > 1 && (
        <div className="row" style={{ marginBottom: 8 }}>
          {docs.map((d) => (
            <button
              key={d.id}
              className={`btn btn-sm${d.id === docId ? ' btn-primary' : ''}`}
              onClick={() => setDocId(d.id)}
            >
              {d.original_filename || `doc #${d.id}`}
            </button>
          ))}
        </div>
      )}
      {doc && !url && <p className="muted">Loading scan…</p>}
      {url && isImage && (
        <div className="dv-canvas">
          <div className="dv-frame">
            <img
              src={url}
              alt={doc.original_filename}
              className="dv-img"
              onLoad={(e) => setDims([e.target.naturalWidth, e.target.naturalHeight])}
            />
            {boxes.map(({ field, style }) => (
              <div
                key={field.id}
                className={`dv-box${activeFieldId === field.id ? ' active' : ''}`}
                style={style}
                onClick={() => onSelectField && onSelectField(field.id)}
                title={`${field.field_type.replace(/_/g, ' ')}: ${field.current_value ?? 'UNKNOWN'}`}
              >
                <span className="dv-tag">{field.field_type.replace(/_/g, ' ')}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {url && isPdf && (
        <>
          <object data={url} type="application/pdf" width="100%" height={expanded ? 'calc(100vh - 90px)' : 420} style={{ borderRadius: 4 }}>
            <p><a href={url} target="_blank" rel="noreferrer">open the PDF ↗</a></p>
          </object>
          <p className="muted" style={{ fontSize: 12 }}>
            PDF preview — highlight overlays are drawn on image scans; the evidence crops still show each value's exact pixels.
          </p>
        </>
      )}
      {url && !isImage && !isPdf && (
        <p className="muted">Preview not supported for {doc.mime} — <a href={url} target="_blank" rel="noreferrer">download ↗</a>.</p>
      )}
      {doc && (
        <p className="muted" style={{ fontSize: 12, margin: '8px 0 0' }}>
          <span className="mono">{doc.sha256.slice(0, 16)}…</span> · {doc.mime} · {doc.page_count} page(s)
        </p>
      )}
      <p className="muted" style={{ fontSize: 12, margin: '6px 0 0' }}>
        {located > 0
          ? <>📍 <b>{located}</b> of {visionFields || boxes.length} read fields located on this scan — click a box or a value to link them.</>
          : visionFields > 0
            ? 'Fields were read from this document on a text path — pixel locations are recorded when values are read directly from scan pixels (vision extraction).'
            : 'Highlight boxes appear when fields are read directly from the scan pixels (vision extraction). Every value still links to its evidence via the Evidence button.'}
      </p>
    </>
  )

  return (
    <div className="doc-viewer">
      <div className="row" style={{ marginBottom: 6 }}>
        <h2 style={{ margin: 0 }}>Source document</h2>
        <span className="right row" style={{ gap: 6 }}>
          <button className="btn btn-sm" onClick={() => setExpanded(true)} disabled={!url}>⤢ Expand</button>
        </span>
      </div>
      {body}

      {expanded && (
        <div className="dv-fullscreen" onClick={() => setExpanded(false)}>
          <div className="dv-fullscreen-head">
            <b>{doc?.original_filename}</b>
            <span className="muted" style={{ fontSize: 12 }}>click anywhere or press Esc to close</span>
            <button className="btn btn-sm right" onClick={() => setExpanded(false)}>Close</button>
          </div>
          <div className="dv-canvas" onClick={(e) => e.stopPropagation()}>
            <div className="dv-frame" style={{ width: 'fit-content', margin: '0 auto' }}>
              <img
                src={url}
                alt={doc?.original_filename}
                className="dv-img dv-img-full"
                onLoad={(e) => setDims([e.target.naturalWidth, e.target.naturalHeight])}
              />
              {boxes.map(({ field, style }) => (
                <div
                  key={field.id}
                  className={`dv-box${activeFieldId === field.id ? ' active' : ''}`}
                  style={style}
                  onClick={(e) => { e.stopPropagation(); onSelectField && onSelectField(field.id) }}
                  title={`${field.field_type.replace(/_/g, ' ')}: ${field.current_value ?? 'UNKNOWN'}`}
                >
                  <span className="dv-tag">{field.field_type.replace(/_/g, ' ')}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
