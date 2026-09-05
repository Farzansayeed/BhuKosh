"""Versioned rule registry (plan §5: rules live in CODE, not the DB).

Every rule is deterministic, pure, and returns findings the validation service
turns into validation_results rows (append-only) and, where flagged, anomalies.
Anomaly explanations always carry RULE → EVIDENCE → CALCULATION → RELATED
RECORD → RECOMMENDED ACTION (plan §6: never a bare "ANOMALY DETECTED").

UP profile v1 (plan §6 line 190 subset, deterministic on our fields):
  R-KHASRA-PATTERN  warn   khasra/khata pattern sanity
  R-AREA-FORMAT     warn   area must parse under the plan's २-४० = 2.40 convention
  R-VILLAGE-MISSING info   village identity absent
  R-UNKNOWN-FIELD   error  any field the engine refused to value (UNKNOWN)
  R-AREA-JUMP       error  cross-document area jump > 25% (plan §6 MVP join)
"""
import re

from .primitives import khasra_pattern_ok, normalize_digits, parse_area_bigha

REGISTRY_VERSION = "1"

POST_VALIDATION_STATES = ("VALIDATED", "REVIEW_REQUIRED", "VERIFIED", "OFFICER_CERTIFIED")


def _field(fields: list[dict], ftype: str) -> dict | None:
    return next((f for f in fields if f["field_type"] == ftype), None)


def _pass(field_id: int | None, detail: dict) -> dict:
    return {"field_id": field_id, "outcome": "pass", "detail": detail}


def _fail(field_id: int | None, severity: str, detail: dict, explanation: dict | None = None) -> dict:
    return {
        "field_id": field_id,
        "outcome": "fail",
        "severity": severity,
        "detail": detail,
        "explanation": explanation,
    }


def r_khasra_pattern(rec: dict, fields: list[dict], others: list[dict]) -> list[dict]:
    f = _field(fields, "khasra_no")
    v = (f["current_value"] or "").strip() if f else None
    if not v:
        return [_pass(f["id"] if f else None, {"skipped": "no value"})]
    if khasra_pattern_ok(v):
        return [_pass(f["id"], {"pattern": "ok"})]
    return [_fail(f["id"], "warn", {"value": v, "expected": "digits with optional / or - separators"})]


def r_area_format(rec: dict, fields: list[dict], others: list[dict]) -> list[dict]:
    f = _field(fields, "area_raw")
    v = (f["current_value"] or "").strip() if f else None
    if not v:
        return [_pass(f["id"] if f else None, {"skipped": "no value"})]
    if parse_area_bigha(v) is not None:
        return [_pass(f["id"], {"parsed_bigha": parse_area_bigha(v)})]
    return [_fail(f["id"], "warn", {"value": v, "expected": "bigha-biswa(-biswansi), e.g. २-४० bigha"})]


def r_village_missing(rec: dict, fields: list[dict], others: list[dict]) -> list[dict]:
    f = _field(fields, "village")
    v = (f["current_value"] or "").strip() if f else None
    if v and v != "UNKNOWN":
        return [_pass(f["id"] if f else None, {"village": v})]
    return [_fail(f["id"] if f else None, "info", {"village": v or None, "note": "village identity absent"})]


def r_unknown_field(rec: dict, fields: list[dict], others: list[dict]) -> list[dict]:
    findings = []
    for f in fields:
        if f["current_value"] is None:
            findings.append(
                _fail(
                    f["id"], "error",
                    {"field_type": f["field_type"], "candidate_is_unknown": True},
                    {
                        "rule": "R-UNKNOWN-FIELD",
                        "evidence": [f["id"]],
                        "calculation": f"field '{f['field_type']}' has no value (UNKNOWN)",
                        "related_record_id": None,
                        "recommended_action": "Re-extract the page or correct the field manually before approval.",
                    },
                )
            )
    if not findings:
        return [_pass(None, {"unknown_fields": 0})]
    return findings


def r_area_jump(rec: dict, fields: list[dict], others: list[dict]) -> list[dict]:
    """MVP cross-document join (plan §6): same (village, khasra), |Δ| > 25%.

    Fires on the record being validated (the newer one); the earlier record is
    referenced as related_record_id.
    """
    own = _field(fields, "area_raw")
    own_v = (own["current_value"] or "").strip() if own else None
    own_area = parse_area_bigha(own_v)
    if own_area is None:
        return [_pass(own["id"] if own else None, {"skipped": "own area unparseable/absent"})]

    comparable = [
        o for o in others
        if o["current_state"] in POST_VALIDATION_STATES and parse_area_bigha(o["area_raw"])
    ]
    if not comparable:
        return [_pass(own["id"], {"compared": 0})]

    findings = []
    for o in comparable:
        other_area = parse_area_bigha(o["area_raw"])
        delta_pct = (own_area - other_area) / other_area * 100
        if abs(delta_pct) > 25:
            findings.append(
                _fail(
                    own["id"], "error",
                    {"own_bigha": own_area, "other_bigha": other_area, "delta_pct": round(delta_pct, 1)},
                    {
                        "rule": "R-AREA-JUMP",
                        "evidence": [own["id"], o["area_field_id"]],
                        "calculation": f"{other_area:.2f}→{own_area:.2f} bigha ({delta_pct:+.0f}%)",
                        "related_record_id": o["record_id"],
                        "recommended_action": "Verify both scans; a legitimate transfer/mutation may explain the jump — route for officer review.",
                    },
                )
            )
    if not findings:
        return [_pass(own["id"], {"compared": len(comparable), "max_abs_delta_pct": 25.0})]
    return findings


RULES: dict[str, dict] = {
    "R-KHASRA-PATTERN": {"version": REGISTRY_VERSION, "run": r_khasra_pattern},
    "R-AREA-FORMAT":    {"version": REGISTRY_VERSION, "run": r_area_format},
    "R-VILLAGE-MISSING": {"version": REGISTRY_VERSION, "run": r_village_missing},
    "R-UNKNOWN-FIELD":  {"version": REGISTRY_VERSION, "run": r_unknown_field},
    "R-AREA-JUMP":      {"version": REGISTRY_VERSION, "run": r_area_jump},
}

# Rules whose failures create anomalies (others stay validation_results-only).
ANOMALY_RULES = {"R-UNKNOWN-FIELD", "R-AREA-JUMP"}

# Sanitize any value echoed into findings (defense against odd scan bytes).
def _safe(s: str | None, limit: int = 120) -> str | None:
    if s is None:
        return None
    return re.sub(r"[\x00-\x1f]", "", normalize_digits(s))[:limit]
