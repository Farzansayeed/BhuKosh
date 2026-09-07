import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import CropImage from '../CropImage'
import { useAuth } from '../auth'
import { useI18n } from '../i18n'

export default function UploadPage() {
  const { role } = useAuth()
  const { t } = useI18n()
  const canWrite = ['operator', 'checker', 'certifier', 'admin'].includes(role)

  const [registerRef, setRegisterRef] = useState(`REG-${new Date().toISOString().slice(0, 10)}`)
  const [centre, setCentre] = useState('')
  const [device, setDevice] = useState('')
  const [file, setFile] = useState(null)
  const [text, setText] = useState('')
  const [visionMode, setVisionMode] = useState(true)
  const [engines, setEngines] = useState([])
  const [engine, setEngine] = useState('gemini')
  const [busy, setBusy] = useState(false)
  const [step, setStep] = useState('')
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [storedDoc, setStoredDoc] = useState(null)
  const [result, setResult] = useState(null)
  const [recordId, setRecordId] = useState(null)

  useEffect(() => {
    let alive = true
    api('/engines')
      .then((list) => {
        if (!alive) return
        setEngines(list)
        // default to the first available engine (catalog order = recommendation order)
        const avail = list.find((e) => e.available && e.id !== 'groq')
        if (avail) setEngine(avail.id)
      })
      .catch(() => {}) // selector is optional; extraction defaults to gemini
    return () => { alive = false }
  }, [])

  // Scanned PDFs are read natively only by Gemini — the OpenRouter/Groq image
  // path cannot accept PDF data-URLs (the backend 422s them upfront). Steer
  // the engine choice before the run instead of failing mid-pipeline.
  const isPdfFile = (f) => !!f && (f.type === 'application/pdf' || /\.pdf$/i.test(f.name || ''))
  const isPdf = isPdfFile(file)
  const ensureEngineForFile = (f, vision) => {
    if (f && vision && isPdfFile(f) && engine !== 'gemini') {
      setEngine('gemini')
      setNotice(t('pdf_gemini_only'))
    }
  }

  if (!canWrite) {
    return <><h1>{t('upload_title')}</h1><div className="error-box">{t('upload_denied', { role })}</div></>
  }

  const run = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setNotice(null)
    setResult(null)
    setRecordId(null)
    setStoredDoc(null)
    let phase = ''
    try {
      phase = t('step_manifest')
      setStep(phase)
      const m = await api('/intake', {
        method: 'POST',
        body: { register_ref: `${registerRef}-${Date.now()}`, centre: centre || 'UI', device: device || 'UI', expected_count: 1 },
      })

      phase = t('step_uploading')
      setStep(phase)
      const fd = new FormData()
      fd.append('manifest_id', String(m.id))
      fd.append('file', file)
      let d
      try {
        d = await api('/documents', { method: 'POST', formData: fd })
      } catch (err) {
        // Idempotent custody: a byte-identical upload 409s with the stored id
        // (renaming a file doesn't change its bytes). Resume on the stored
        // copy instead of dead-ending — the evidence chain keeps one row.
        if (err.status !== 409 || !err.problem?.existing_id) throw err
        d = await api(`/documents/${err.problem.existing_id}`)
        setNotice(t('dup_doc', { id: d.id }))
      }
      setStoredDoc(d)

      phase = t('step_registering')
      setStep(phase)
      let p
      try {
        p = await api(`/documents/${d.id}/pages`, { method: 'POST', body: { seq_no: 1, sha256: d.sha256 } })
      } catch (err) {
        if (err.status !== 409 || !err.problem?.existing_id) throw err
        const pages = await api(`/documents/${d.id}/pages`)
        p = pages.find((pg) => pg.seq_no === 1)
        if (!p) throw err
      }

      let ex
      if (visionMode) {
        phase = t('step_vision')
        setStep(phase)
        const v = await api(`/pages/${p.id}/extract-image?engine=${encodeURIComponent(engine)}`, { method: 'POST' })
        ex = { run: { id: v.run_id, status: v.status }, candidates: [], crops: v.crops || {}, fields: v.fields }
      } else {
        phase = t('step_extract')
        setStep(phase)
        ex = await api(`/documents/${d.id}/extract`, { method: 'POST', body: { text, page_id: p.id, engine } })
      }

      setResult({ manifest: m, document: d, page: p, ...ex })
      setStep('')
    } catch (err) {
      setError(`${t('failed', { msg: '' }).replace(/:$/, '')} ${phase}: ${err.message}`)
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
      <h1>{t('upload_title')}</h1>
      <p className="sub">{t('upload_sub')}</p>

      <form onSubmit={run} className="card">
        <h2>{t('step1_manifest')}</h2>
        <div className="row">
          <div className="field" style={{ flex: 1, minWidth: 180 }}>
            <label>{t('register_reference')}</label>
            <input value={registerRef} onChange={(e) => setRegisterRef(e.target.value)} required />
          </div>
          <div className="field" style={{ flex: 1, minWidth: 140 }}>
            <label>{t('centre')}</label>
            <input value={centre} onChange={(e) => setCentre(e.target.value)} placeholder="e.g. Tehsil Salempr" />
          </div>
          <div className="field" style={{ flex: 1, minWidth: 140 }}>
            <label>{t('device')}</label>
            <input value={device} onChange={(e) => setDevice(e.target.value)} placeholder="e.g. Scanner-01" />
          </div>
        </div>

        <h2>{t('step2_mode')}</h2>
        <div className="field">
          <label>{t('page_image')}</label>          <input type="file" accept="image/png,image/jpeg,application/pdf"
                 onChange={(e) => { const f = e.target.files[0] ?? null; setFile(f); setNotice(null); ensureEngineForFile(f, visionMode) }} required />
        </div>
        <div className="field">
          <label>
            <input type="radio" checked={!visionMode} onChange={() => setVisionMode(false)} /> {t('text_mode')}
          </label>
          <label>
            <input type="radio" checked={visionMode} onChange={() => { setVisionMode(true); ensureEngineForFile(file, true) }} /> {t('vision_mode')}
          </label>
        </div>
        {!visionMode && (
          <div className="field">
            <label>{t('register_line_label')}</label>
            <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)}
                      placeholder="राम प्रसाद पुत्र श्याम लाल, खसरा २३४, क्षेत्र २-४० बीघा, ग्राम सलेमपुर" required />
          </div>
        )}

        {engines.length > 0 && (
          <div className="field">
            <label>{t('engine_choice')}</label>
            <div className="engine-grid">
              {engines.map((e) => {
                const pdfLocked = isPdf && visionMode && e.id !== 'gemini'
                return (
                <label key={e.id} className={`engine-card${engine === e.id ? ' active' : ''}${e.available && !pdfLocked ? '' : ' disabled'}`}>
                  <input type="radio" name="engine" checked={engine === e.id}
                         disabled={!e.available || (visionMode && e.id === 'groq') || pdfLocked} onChange={() => setEngine(e.id)} />
                  <span className="engine-head">
                    <b>{e.label}</b>
                    <span className="mono muted">{e.model}</span>
                    {!e.available && <span className="badge REJECTED">{t('engine_unavailable')}</span>}
                    {e.available && visionMode && e.id === 'groq' && <span className="badge">{t('engine_text_only')}</span>}
                    {e.available && pdfLocked && <span className="badge">{t('engine_pdf_locked')}</span>}
                  </span>
                  <span className="engine-best">{t('engine_best_for')}: {e.best_for}</span>
                  <ul className="engine-list">
                    {e.strengths.map((s) => <li key={s}>+ {s}</li>)}
                    {e.weaknesses.map((w) => <li key={w} className="engine-weak">− {w}</li>)}
                  </ul>
                </label>
                )
              })}
            </div>
          </div>
        )}

        <button className="btn btn-primary" disabled={busy || !file || (!visionMode && !text.trim())}>
          {busy ? (step || t('working')) : visionMode ? t('run_vision_pipeline') : t('run_text_pipeline')}
        </button>
      </form>

      {error && <div className="error-box">{error}</div>}
      {error && storedDoc && (
        <div className="card">
          <p className="muted">{t('stored_note', { id: storedDoc.id, sha: storedDoc.sha256.slice(0, 16) })}</p>
        </div>
      )}
      {notice && !error && <div className="ok-box">{notice}</div>}

      {result && (
        <div className="card">
          <h2>{t('extraction_run')} #{result.run.id} — <span className={`badge ${result.run.status}`}>{result.run.status}</span></h2>
          <p className="muted">
            {t('evidence_chain')} #{result.manifest.id} → {t('source_document')} #{result.document.id} (sha <span className="mono">{result.document.sha256.slice(0, 16)}…</span>) → {t('page')} #{result.page.id}
          </p>
          <div className="detail-grid">
            {result.candidates.length > 0
              ? result.candidates.map((c) => (
                <div key={c.id} className="value-card">
                  <div className="fv-label">{c.field_type.replace(/_/g, ' ')}</div>
                  <div className={`fv-value${c.is_unknown ? ' unknown' : ''}`}>{c.value ?? t('unknown')}</div>
                  <div className="muted mono">{t('raw')} {c.raw_value ?? '—'}{c.confidence != null ? ` · ${t('col_confidence').toLowerCase()} ${c.confidence}` : ''}</div>
                </div>
              ))
              : Object.entries(result.fields || {}).map(([k, v]) => (
                <div key={k} className="value-card">
                  <div className="fv-label">{k.replace(/_/g, ' ')}</div>
                  <div className={`fv-value${v == null ? ' unknown' : ''}`}>{v ?? t('unknown')}</div>
                  {result.crops?.[k] != null && <CropImage cropId={result.crops[k]} maxWidth={220} />}
                </div>
              ))}
          </div>
          {recordId
            ? <div className="ok-box">{t('record_created')} <Link to={`/records/${recordId}`}>{t('open_record', { id: recordId })}</Link></div>
            : <button className="btn btn-primary mt" onClick={project} disabled={busy}>{t('create_from_run')}</button>}
        </div>
      )}
    </>
  )
}
