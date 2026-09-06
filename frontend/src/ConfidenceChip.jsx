import React from 'react'
import { useI18n } from './i18n'

// Document-level confidence (PS #11): the WORST self-reported field score on
// the record — a record is exactly as trustworthy as its least-certain
// reading. Human-corrected fields don't count (a human settled those).
export default function ConfidenceChip({ confidence }) {
  const { t } = useI18n()
  if (!confidence) return null
  const { min_confidence: mc, band, scored_fields: sf, total_fields: tf } = confidence
  if (mc == null) {
    return (
      <span className="chip" title={t('conf_none_title')}>
        conf —
      </span>
    )
  }
  const label = t('conf_title', { pct: Math.round(mc * 100), sf, tf })
  return (
    <span className={`chip conf-${band}`} title={label}>
      conf {Math.round(mc * 100)}%
    </span>
  )
}
