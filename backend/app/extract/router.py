"""/extract — server-held AI extraction (prototype slice of the EXTRACTION stage).

One shared engine: every user calls this route; the Gemini key exists only in
this backend's process/env. Per-user rate limiting + usage logging come free
via the api_usage table.
"""
import psycopg
from fastapi import APIRouter, Depends
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from ..auth.dependencies import require_roles
from ..config import get_settings
from ..db import conninfo
from . import gemini_client, rate_limit

router = APIRouter(prefix="/extract", tags=["extract"])

# Land-record register fields (prototype scope; grows with the full EXTRACTION stage).
KHASRA_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "khasra_no": {"type": "STRING", "nullable": True},
        "owner_name": {"type": "STRING", "nullable": True},
        "area_raw": {"type": "STRING", "nullable": True},
        "village": {"type": "STRING", "nullable": True},
    },
    "required": ["khasra_no", "owner_name", "area_raw"],
}

PROMPT = (
    "You are reading a line/section from an Indian land-record register "
    "(khatauni/jamabandi), possibly handwritten or Hindi/English mixed. "
    "Extract exactly these fields from the record text below:\n"
    "- khasra_no: the khewat/khasra/gata number (खसरा/खेवट/गाता नंबर)\n"
    "- owner_name: the cultivator/owner name (किसान/स्वामी/खातेदार का नाम)\n"
    "- area_raw: the land area exactly as printed (रकबा/क्षेत्रफल, e.g. '२-४० bigha')\n"
    "- village: the village name (गाँव/मौजा)\n"
    "Keep each value in its printed script (Devanagari stays Devanagari). "
    "If a field is unreadable or absent, return null for it instead of guessing."
)


class ExtractIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@router.post("")
def extract(
    body: ExtractIn,
    user: dict = Depends(require_roles("operator", "checker", "certifier", "admin")),
) -> dict:
    rate_limit.enforce(user["username"])

    parsed: dict | None = None
    status = "ok"
    try:
        parsed = gemini_client.structured_extract(f"{PROMPT}\n\nRECORD TEXT:\n{body.text}", KHASRA_SCHEMA)
        return parsed
    except Exception:
        status = "error"
        raise
    finally:
        try:  # usage log must never break or swallow the real response
            with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
                conn.execute(
                    """INSERT INTO api_usage (username, route, status, model, prompt_chars, result_json)
                       VALUES (%s, '/extract', %s, %s, %s, %s)""",
                    (
                        user["username"],
                        status,
                        get_settings().gemini_model,
                        len(body.text),
                        Jsonb(parsed) if parsed is not None else None,
                    ),
                )
                conn.commit()
        except Exception:  # noqa: BLE001
            pass
