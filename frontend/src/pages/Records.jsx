import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import ConfidenceChip from '../ConfidenceChip'
import { useI18n } from '../i18n'

// Records list: server-side search (Postgres trigram index, ILIKE fallback),
// server-side sorting on whitelisted columns, state filter. Document-level
// confidence chip = the worst field score (corrected fields excluded).
const COLUMNS = [
  { key: 'id', key_id: 'col_id' },
  { key: 'village', key_id: 'col_village' },
  { key: 'khasra', key_id: 'col_khasra' },
  { key: 'state', key_id: 'col_state' },
  { key: 'confidence', key_id: 'col_confidence' },
  { key: 'version', key_id: 'col_version' },
  { key: 'claim', key_id: 'col_claim' },
  { key: 'updated', key_id: 'col_updated' },
]

const STATES = ['', 'INGESTED', 'EXTRACTED', 'VALIDATED', 'REVIEW_REQUIRED', 'VERIFIED', 'OFFICER_CERTIFIED', 'ARCHIVED', 'REJECTED', 'QUARANTINED']

export default function RecordsPage() {
  const { t } = useI18n()
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(null)
  const [state, setState] = useState('')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState({ by: 'id', order: 'desc' })
  const [searching, setSearching] = useState(false)

  // Live search: fires as you type (250ms debounce), aborts the previous
  // in-flight request so results always match what's in the box, and keeps
  // the current rows visible (dimmed) while a new request runs.
  useEffect(() => {
    const ctrl = new AbortController()
    const t = setTimeout(async () => {
      setSearching(true)
      setError(null)
      try {
        const qs = new URLSearchParams()
        if (state) qs.set('state', state)
        if (search.trim()) qs.set('search', search.trim())
        qs.set('sort_by', sort.by)
        qs.set('order', sort.order)
        qs.set('limit', '200')
        const data = await api(`/records?${qs.toString()}`, { signal: ctrl.signal })
        setRows(data)
      } catch (e) {
        if (e.name !== 'AbortError') setError(e.message)
      } finally {
        setSearching(false)
      }
    }, 250)
    return () => { clearTimeout(t); ctrl.abort() }
  }, [state, search, sort.by, sort.order])

  const submitSearch = (e) => {
    e.preventDefault()  // Enter still works; the debounce usually beats it
  }

  const flip = (key) => setSort((s) =>
    s.by === key
      ? { ...s, order: s.order === 'asc' ? 'desc' : 'asc' }
      : { by: key, order: (key === 'id' || key === 'version') ? 'desc' : 'asc' }
  )

  return (
    <div>
      <h1>{t('records_title')}</h1>
      <p className="sub">{t('records_sub')}</p>

      <div className="row" style={{ margin: '10px 0 14px', alignItems: 'flex-end' }}>
        <form onSubmit={submitSearch} className="row" style={{ gap: 6, flex: '1 1 260px', maxWidth: 440 }}>
          <div className="field" style={{ marginBottom: 0, flex: 1 }}>
            <input
              placeholder={t('search_placeholder')}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          {searching && <span className="muted" style={{ fontSize: 12 }}>{t('searching')}</span>}
          {search && (
            <button className="btn btn-sm" type="button" onClick={() => setSearch('')}>{t('clear')}</button>
          )}
        </form>
        <div className="field" style={{ marginBottom: 0 }}>
          <select value={state} onChange={(e) => setState(e.target.value)}>
            {STATES.map((s) => (
              <option key={s} value={s}>{s === '' ? t('all_states') : s.replace('_', ' ')}</option>
            ))}
          </select>
        </div>
        <span className="muted">{rows ? t('n_records', { n: rows.length }) : t('loading')}</span>
      </div>

      {error && <div className="error-box">{error}</div>}

      {rows && rows.length === 0 && (
        <div className="card muted">
          {t('no_records', {
            state: state ? t('no_records_state', { state }) : '',
            search: search ? t('no_records_search', { search }) : '',
          })}
        </div>
      )}

      {rows && rows.length > 0 && (
        <div className="card" style={{ padding: 0, overflowX: 'auto', opacity: searching ? 0.6 : 1, transition: 'opacity 150ms' }}>
          <table>
            <thead>
              <tr>
                {COLUMNS.map((c) => (
                  <th key={c.key}>
                    {c.key === 'confidence' || c.key === 'claim' ? (
                      t(c.key_id)
                    ) : (
                      <button
                        onClick={() => flip(c.key)}
                        style={{ all: 'unset', cursor: 'pointer', font: 'inherit', color: 'inherit' }}
                        title={t('sort')}
                      >
                        {t(c.key_id)}{sort.by === c.key ? (sort.order === 'asc' ? ' ↑' : ' ↓') : ''}
                      </button>
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td><Link to={`/records/${r.id}`}>#{r.id}</Link></td>
                  <td>{r.village_code}</td>
                  <td className="mono">{r.khasra_no}</td>
                  <td><span className={`badge ${r.current_state}`}>{r.current_state.replace('_', ' ')}</span></td>
                  <td><ConfidenceChip confidence={r.confidence} /></td>
                  <td className="mono">{r.record_version}</td>
                  <td>{r.claim_owner ? <span className="badge OPEN">{r.claim_owner}</span> : <span className="muted">—</span>}</td>
                  <td className="muted">{r.updated_at ? new Date(r.updated_at).toLocaleString() : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
