import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { useI18n } from '../i18n'

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
  const { t } = useI18n()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api('/stats').then(setData).catch((e) => setError(e.message))
  }, [])

  if (error) return <div className="card error-card">{t('failed_stats', { msg: error })}</div>
  if (!data) return <div className="card">{t('loading')}</div>

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
      <h2>{t('dashboard')}</h2>

      <div className="stat-grid">
        <Stat label={t('records_processed')} value={records.total} sub={t('pipeline_states', { n: Object.keys(records.by_state).length })} />
        <Stat
          label={t('extraction_accuracy')}
          value={fields.accuracy_percent == null ? '—' : `${fields.accuracy_percent}%`}
          sub={t('corrections_of_fields', { n: fields.corrections, total: fields.total })}
        />
        <Stat label={t('pending_verification')} value={records.pending_verification} sub="REVIEW_REQUIRED" />
        <Stat label={t('open_anomalies')} value={openAnomalies} sub={t('from_rules')} />
        <Stat label={t('extraction_runs')} value={okRuns} sub={failedRuns ? t('n_failed', { n: failedRuns }) : t('none_failed')} />
      </div>

      <div className="dash-cols">
        <div className="card">
          <h3>{t('records_by_state')}</h3>
          {Object.keys(records.by_state).length === 0 && <p className="muted">{t('no_records_yet')}</p>}
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
          <h3>{t('anomalies_by_rule')}</h3>
          {anomalies_by_rule.length === 0 && <p className="muted">{t('no_anomalies')}</p>}
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
          <h3>{t('engine')}</h3>
          {extraction_runs.length === 0 && <p className="muted">{t('no_runs')}</p>}
          {extraction_runs.map((r, i) => (
            <div className="mini-row" key={i}>
              <span className="mono">{r.engine_name} {r.engine_version || ''}</span>
              <span className={`chip ${r.status === 'SUCCEEDED' ? 'sev-ok' : 'sev-error'}`}>{r.status}</span>
              <span className="bar-n">{r.n}</span>
              {r.avg_seconds != null && <span className="muted">{t('avg', { n: r.avg_seconds })}</span>}
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <h3>{t('region_progress')}</h3>
        {progress.length === 0 && <p className="muted">{t('no_regions')}</p>}
        {progress.map((p, i) => (
          <div className="bar-row" key={i}>
            <span className="bar-label">{p.region}</span>
            <span className="bar-track">
              <span className="bar-fill" style={{ width: `${(100 * p.records) / maxRegion}%` }} />
            </span>
            <span className="bar-n">{p.records}</span>
            <span className="muted">
              {t('confirmed_pending', { c: p.confirmed, p: p.pending_review })}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
