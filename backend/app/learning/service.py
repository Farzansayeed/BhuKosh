"""PS #13 — the AI learning loop.

Every CORRECTION decision is a labeled example: what the engine read (before)
vs what the human certified (after) for a named field. recent_corrections()
reads the latest ones; build_hints_block() renders them as few-shot guidance
injected into future extraction prompts, so the same class of mistake is less
likely to repeat. Corrections are sanitised before entering a prompt, and the
loop is auditable end-to-end: hints trace back to decision rows.
"""
import psycopg
from psycopg.rows import dict_row

from ..db import conninfo

MAX_HINTS = 8  # few-shot budget: enough signal, no prompt bloat


def recent_corrections(limit: int = MAX_HINTS) -> list[dict]:
    """Latest human corrections with the field they fixed, newest first."""
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        return conn.execute(
            """SELECT hd.id AS decision_id, hd.field_id, fv.field_type,
                      hd.before_value ->> 'value' AS before_value,
                      hd.after_value  ->> 'value' AS after_value,
                      hd.reason, hd.actor_id, hd.created_at
               FROM human_decisions hd
               JOIN field_values fv ON fv.id = hd.field_id
               WHERE hd.decision_type = 'CORRECTION'
                 AND hd.after_value ->> 'value' IS NOT NULL
               ORDER BY hd.id DESC
               LIMIT %s""",
            (limit,),
        ).fetchall()


def _safe(text: str | None, limit: int = 80) -> str:
    """Correction values come from scans via humans — strip control chars,
    cap length, so nothing weird can bend the prompt."""
    if text is None:
        return ""
    return "".join(ch for ch in str(text) if ch.isprintable())[:limit].strip()


def build_hints_block(corrections: list[dict] | None = None) -> str:
    """Render corrections as a prompt block. Empty string when none exist,
    so prompts are byte-identical to the pre-learning behavior at first run."""
    if corrections is None:
        corrections = recent_corrections()
    if not corrections:
        return ""
    lines = [
        "REVIEWER FEEDBACK — corrections humans made to previous extractions.",
        "Read these fields with the same care; do not repeat these mistakes:",
    ]
    for c in corrections:
        before, after = _safe(c["before_value"]), _safe(c["after_value"])
        if not after or before == after:
            continue
        field = _safe(c["field_type"], 40)
        why = _safe(c["reason"], 100)
        line = f'- {field}: the engine read "{before}" but the correct value is "{after}"'
        if why:
            line += f" ({why})"
        lines.append(line)
    if len(lines) == 2:
        return ""
    lines.append("Apply this feedback only where the document supports it.")
    return "\n".join(lines)


def hints_for_display() -> dict:
    """What the demo/audit endpoint shows: the live prompt block plus the
    decision trail behind it."""
    corrections = recent_corrections()
    return {
        "hints_count": len([c for c in corrections if _safe(c["before_value"]) != _safe(c["after_value"])]),
        "prompt_block": build_hints_block(corrections),
        "sources": [
            {
                "decision_id": c["decision_id"],
                "field_type": c["field_type"],
                "before": c["before_value"],
                "after": c["after_value"],
                "reason": c["reason"],
                "actor": c["actor_id"],
            }
            for c in corrections
        ],
    }
