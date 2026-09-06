import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import ConfidenceChip from '../ConfidenceChip'

const STATES = ['', 'REVIEW_REQUIRED', 'EXTRACTED', 'VALIDATED', 'VERIFIED', 'OFFICER_CERTIFIED', 'REJECTED', 'ARCHIVED']

export default function RecordsPage() {
  const [rows, setRows] = useState(null)
  const [state, setState] = useState('')
  const [error, setError] = useState(null)

  const load = (s) => {
    setRows(null)
    api(`/records${s ? `?state=${s}` : ''}`)
      .then(setRows)
      .catch((e) => setError(e.message))
  }
  useEffect(() => { load(state) }, [state])

  return (
    <>
      <h1>Records</h1>
      <p className="sub">Every land record, with its lifecycle state. Any value → Record view → one click to evidence.</p>

      <div className="row" style={{ marginBottom: 14 }}>
        <select className="field" style={{ width: 220 }} value={state} onChange={(e) => setState(e.target.value)}>
          {STATES.map((s) => (
            <option key={s} value={s}>{s === '' ? 'All states' : s.replace('_', ' ')}</option>
          ))}
        </select>
        <span className="muted">{rows ? `${rows.length} record(s)` : 'loading…'}</span>
      </div>

      {error && <div className="error-box">{error}</div>}

      {rows && rows.length === 0 && <div className="card muted">No records{state ? ` in ${state}` : ''}. Upload a register page to create the first one.</div>}

      {rows && rows.length > 0 && (
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>ID</th><th>Village</th><th>Khasra</th><th>State</th><th>Confidence</th><th>Ver</th><th>Claim</th><th>Updated</th>
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
    </>
  )
}
