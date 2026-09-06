"""Multilingual output of scanned values (PS #7 — the OUTPUT side).

Extraction stays script-faithful: a Gujarati register yields ગુજરાતી values.
This module adds per-language RENDERINGS of those values for operators who
don't read the source script:

  POST /records/{id}/translate  — one Gemini pass renders every non-empty
  field value into EN/HI/GU; results land in field_values.translations
  (JSONB, migration 011). The original value is never modified — the
  translation is advisory output, and every rendering coexists with the
  evidence so nothing is lost or overwritten.

GET /records/{id} includes each field's translations, so the UI can show the
value in the operator's chosen UI language when available.
"""
import json

import psycopg
from fastapi import APIRouter, Depends
from psycopg.rows import dict_row

from .auth.permissions import require_permission
from .config import get_settings
from .db import conninfo
from .errors import Problem
from .extract.gemini_client import structured_extract
from .extract.schema import ALL_FIELDS

router = APIRouter(tags=["i18n-output"])

LANGS = ("en", "hi", "gu")
LANG_NAMES = {"en": "English", "hi": "Hindi (Devanagari)", "gu": "Gujarati"}

# One object per FIELD, each holding that field's own EN/HI/GU rendering.
_VALUE_REND = {
    "type": "OBJECT",
    "properties": {
        code: {"type": "STRING", "nullable": True} for code in LANGS
    },
    "required": list(LANGS),
}
_TRANSLATE_SCHEMA = {
    "type": "OBJECT",
    "properties": {name: _VALUE_REND for name in ALL_FIELDS},
    "required": list(ALL_FIELDS),
}


def _prompt(values: dict[str, str]) -> str:
    lines = "\n".join(f"- {field}: {value}" for field, value in values.items())
    return (
        "You are rendering values read from an Indian land-record register "
        "(khasra number, owner name, area, village, tehsil, etc.) so that "
        "officials who read different scripts can understand them.\n\n"
        "Return ONE object per field, keyed by the exact field names listed "
        "below (khasra_no, owner_name, area_raw, village, khata_no, survey_no, "
        "tehsil, district, land_classification, ownership_type, mutation_ref, "
        "registration_ref). Each field object holds ONLY that field's own "
        "renderings:\n"
        "- en: English (transliterate names/villages; translate terms like "
        "land classification into English; convert Indic numerals to Western digits)\n"
        "- hi: Hindi in Devanagari script\n"
        "- gu: Gujarati in Gujarati script\n"
        "Numbers and reference codes (khasra/khata/survey numbers, areas, "
        "mutation/registration refs) are script-neutral: repeat them as-is. "
        "Names of people and places are transliterated, never translated. "
        "If a field is not in the Values list, or a rendering is impossible, "
        "return null for it instead of guessing.\n\nValues:\n" + lines
    )


@router.post("/records/{record_id}/translate", status_code=200)
def translate_record(record_id: int, user: dict = Depends(require_permission("extract:run"))) -> dict:
    """Render every current field value into EN/HI/GU (one engine pass)."""
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        fields = conn.execute(
            """SELECT id, field_type, current_value
                 FROM field_values
                WHERE record_id = %s AND current_value IS NOT NULL
                ORDER BY id""",
            (record_id,),
        ).fetchall()
        if not fields:
            raise Problem(404, "Not Found", "Record has no extracted values to translate.")

        values = {f["field_type"]: str(f["current_value"]) for f in fields}
        parsed = structured_extract(_prompt(values), _TRANSLATE_SCHEMA)
        # usage metering stays symmetric with extraction
        _ = get_settings().gemini_model

        updated = 0
        out = {}
        for f in fields:
            obj = parsed.get(f["field_type"])
            renderings = (
                {code: (obj.get(code) or None) for code in LANGS}
                if isinstance(obj, dict)
                else {code: None for code in LANGS}
            )
            out[f["field_type"]] = renderings
            if not any(renderings.values()):
                continue
            conn.execute(
                "UPDATE field_values SET translations = %s WHERE id = %s",
                (json.dumps(renderings), f["id"]),
            )
            updated += 1
        conn.commit()

    return {
        "record_id": record_id,
        "fields_translated": updated,
        "languages": list(LANGS),
        "renderings": out,
    }
