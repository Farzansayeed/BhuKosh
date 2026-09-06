import React, { useEffect, useState } from 'react'
import { api } from '../api'

// PS #16 dashboard: documents processed, extraction accuracy, validation
// status, pending verification, error statistics, region-wise progress.
// Every number comes straight from the audited tables — no decoration.
function Stat({ label, value, sub }) {
  return (
    <div className="card stat-card">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

export default function DashboardPage() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api('/stats').then(setData).catch((e) => setError(e.message))
  }, [])

  if (error) return <div className="card error-card">Failed to load stats: {error}</div>
  if (!data) return <div className="card">Loading…</div>

  const { records, fields, anomalies_by_rule, extraction_runs, progress } = data
  const openAnomalies = anomalies_by_rule
    .filter((a) => a.status === 'OPEN')
    .reduce((s, a) => s + a.n, 0)
  const failedRuns = extraction_runs
    .filter((r) => r.status === 'FAILED')
    .reduce((s, r) => s + r.n, 0)
  const okRuns = extraction_runs
    .filter((r) => r.status === 'SUCCEEDED')
    .reduce((s, r) => s + r.n, 0)
  const maxRegion = Math.max(1, ...progress.map((p) => p.records))

  return (
    <div>
      <h2>Dashboard</h2>

      <div className="stat-grid">
        <Stat label="Records processed" value={records.total} sub={`${Object.keys(records.by_state).length} pipeline states`} />
        <Stat
          label="Extraction accuracy"
          value={fields.accuracy_percent == null ? '—' : `${fields.accuracy_percent}%`}
          sub={`${fields.corrections} correction${fields.corrections === 1 ? '' : 's'} / ${fields.total} fields`}
        />
        <Stat label="Pending verification" value={records.pending_verification} sub="REVIEW_REQUIRED" />
        <Stat label="Open anomalies" value={openAnomalies} sub="from business-rule validation" />
        <Stat label="Extraction runs" value={okRuns} sub={failedRuns ? `${failedRuns} failed` : 'none failed'} />
      </div>

      <div className="dash-cols">
        <div className="card">
          <h3>Records by state</h3>
          {Object.keys(records.by_state).length === 0 && <p className="muted">No records yet.</p>}
          {Object.entries(records.by_state).map(([state, n]) => (
            <div className="bar-row" key={state}>
              <span className="bar-label">{state}</span>
              <span className="bar-track">
                <span className="bar-fill" style={{ width: `${(100 * n) / Math.max(1, records.total)}%` }} />
              </span>
              <span className="bar-n">{n}</span>
            </div>
          ))}
        </div>

        <div className="card">
          <h3>Anomalies by rule</h3>
          {anomalies_by_rule.length === 0 && <p className="muted">No anomalies recorded.</p>}
          {anomalies_by_rule.map((a, i) => (
            <div className="mini-row" key={i}>
              <span className={`chip sev-${a.severity}`}>{a.severity}</span>
              <span className="mono">{a.rule_id}</span>
              <span className="muted">{a.status}</span>
              <span className="bar-n">{a.n}</span>
            </div>
          ))}
        </div>

        <div className="card">
          <h3>Extraction engine</h3>
          {extraction_runs.length === 0 && <p className="muted">No runs yet.</p>}
          {extraction_runs.map((r, i) => (
            <div className="mini-row" key={i}>
              <span className="mono">{r.engine_name} {r.engine_version || ''}</span>
              <span className={`chip ${r.status === 'SUCCEEDED' ? 'sev-ok' : 'sev-error'}`}>{r.status}</span>
              <span className="bar-n">{r.n}</span>
              {r.avg_seconds != null && <span className="muted">{r.avg_seconds}s avg</span>}
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <h3>District / village progress</h3>
        {progress.length === 0 && <p className="muted">No regions yet.</p>}
        {progress.map((p, i) => (
          <div className="bar-row" key={i}>
            <span className="bar-label">{p.region}</span>
            <span className="bar-track">
              <span className="bar-fill" style={{ width: `${(100 * p.records) / maxRegion}%` }} />
            </span>
            <span className="bar-n">{p.records}</span>
            <span className="muted">
              {p.confirmed} confirmed · {p.pending_review} pending
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
