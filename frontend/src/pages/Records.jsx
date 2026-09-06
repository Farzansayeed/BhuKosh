import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import ConfidenceChip from '../ConfidenceChip'

// Records list: server-side search (Postgres trigram index, ILIKE fallback),
// server-side sorting on whitelisted columns, state filter. Document-level
// confidence chip = the worst field score (corrected fields excluded).
const COLUMNS = [
  { key: 'id', label: 'ID' },
  { key: 'village', label: 'Village' },
  { key: 'khasra', label: 'Khasra' },
  { key: 'state', label: 'State' },
  { key: 'confidence', label: 'Confidence' },
  { key: 'version', label: 'Ver' },
  { key: 'claim', label: 'Claim' },
  { key: 'updated', label: 'Updated' },
]

const STATES = ['', 'INGESTED', 'EXTRACTED', 'VALIDATED', 'REVIEW_REQUIRED', 'VERIFIED', 'OFFICER_CERTIFIED', 'ARCHIVED', 'REJECTED', 'QUARANTINED']

export default function RecordsPage() {
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(null)
  const [state, setState] = useState('')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState({ by: 'id', order: 'desc' })

  const load = async () => {
    setError(null)
    setRows(null)
    try {
      const qs = new URLSearchParams()
      if (state) qs.set('state', state)
      if (search.trim()) qs.set('search', search.trim())
      qs.set('sort_by', sort.by)
      qs.set('order', sort.order)
      qs.set('limit', '200')
      setRows(await api(`/records?${qs.toString()}`))
    } catch (e) {
      setError(e.message)
    }
  }

  useEffect(() => { load() }, [state, sort.by, sort.order])

  const submitSearch = (e) => {
    e.preventDefault()
    load()
  }

  const flip = (key) => setSort((s) =>
    s.by === key
      ? { ...s, order: s.order === 'asc' ? 'desc' : 'asc' }
      : { by: key, order: (key === 'id' || key === 'version') ? 'desc' : 'asc' }
  )

  return (
    <div>
      <h1>Records</h1>
      <p className="sub">Every land record, with its lifecycle state. Any value → Record view → one click to evidence.</p>

      <div className="row" style={{ margin: '10px 0 14px', alignItems: 'flex-end' }}>
        <form onSubmit={submitSearch} className="row" style={{ gap: 6, flex: '1 1 260px', maxWidth: 440 }}>
          <div className="field" style={{ marginBottom: 0, flex: 1 }}>
            <input
              placeholder="Search village, khasra, owner, khata, survey…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <button className="btn btn-primary btn-sm" type="submit">Search</button>
          {search && (
            <button className="btn btn-sm" type="button" onClick={() => { setSearch(''); load() }}>Clear</button>
          )}
        </form>
        <div className="field" style={{ marginBottom: 0 }}>
          <select value={state} onChange={(e) => setState(e.target.value)}>
            {STATES.map((s) => (
              <option key={s} value={s}>{s === '' ? 'All states' : s.replace('_', ' ')}</option>
            ))}
          </select>
        </div>
        <span className="muted">{rows ? `${rows.length} record(s)` : 'loading…'}</span>
      </div>

      {error && <div className="error-box">{error}</div>}

      {rows && rows.length === 0 && (
        <div className="card muted">No records{state ? ` in ${state}` : ''}{search ? ` matching “${search}”` : ''}. Upload a register page to create the first one.</div>
      )}

      {rows && rows.length > 0 && (
        <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
          <table>
            <thead>
              <tr>
                {COLUMNS.map((c) => (
                  <th key={c.key}>
                    {c.key === 'confidence' || c.key === 'claim' ? (
                      c.label
                    ) : (
                      <button
                        onClick={() => flip(c.key)}
                        style={{ all: 'unset', cursor: 'pointer', font: 'inherit', color: 'inherit' }}
                        title="Sort"
                      >
                        {c.label}{sort.by === c.key ? (sort.order === 'asc' ? ' ↑' : ' ↓') : ''}
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
