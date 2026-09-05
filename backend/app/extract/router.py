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
from .schema import KHASRA_SCHEMA, PROMPT  # re-exported for probe scripts

router = APIRouter(prefix="/extract", tags=["extract"])

# The prototype route shares the same engine-call budget as evidence-bound
# processing runs — one shared Gemini key means one shared quota bucket.
RATE_ROUTE = "/extraction"


class ExtractIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@router.post("")
def extract(
    body: ExtractIn,
    user: dict = Depends(require_roles("operator", "checker", "certifier", "admin")),
) -> dict:
    rate_limit.enforce(user["username"], route=RATE_ROUTE)

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
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        user["username"],
                        RATE_ROUTE,
                        status,
                        get_settings().gemini_model,
                        len(body.text),
                        Jsonb(parsed) if parsed is not None else None,
                    ),
                )
                conn.commit()
        except Exception:  # noqa: BLE001
            pass
