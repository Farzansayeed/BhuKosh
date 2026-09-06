import React from 'react'

// Document-level confidence (PS #11): the WORST self-reported field score on
// the record — a record is exactly as trustworthy as its least-certain
// reading. Human-corrected fields don't count (a human settled those).
export default function ConfidenceChip({ confidence }) {
  if (!confidence) return null
  const { min_confidence: mc, band, scored_fields: sf, total_fields: tf } = confidence
  if (mc == null) {
    return (
      <span className="chip" title="No engine confidence scores on this record (extracted before confidence scoring, or all fields human-corrected).">
        conf —
      </span>
    )
  }
  const label = `Document confidence: ${Math.round(mc * 100)}% (the weakest field reading; ${sf}/${tf} fields scored)`
  return (
    <span className={`chip conf-${band}`} title={label}>
      conf {Math.round(mc * 100)}%
    </span>
  )
}
