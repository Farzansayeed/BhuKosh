"""Server-held Gemini client — the API key never leaves the backend process.

Callers get schema-locked JSON or an RFC-7807 problem; upstream response bodies
and credentials are never echoed to clients.
"""
import json

import httpx

from ..config import get_settings
from ..errors import Problem

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def structured_extract(text: str, schema: dict, *, timeout: float = 60.0) -> dict:
    """One structured-output call on the configured model. Returns the parsed dict."""
    s = get_settings()
    if not s.gemini_api_key:
        raise Problem(503, "Engine Unavailable", "Extraction engine is not configured on this server.")

    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "temperature": 0,
            "response_mime_type": "application/json",
            "response_schema": schema,
        },
    }
    try:
        resp = httpx.post(
            f"{BASE_URL}/models/{s.gemini_model}:generateContent",
            headers={"x-goog-api-key": s.gemini_api_key, "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
        )
    except httpx.HTTPError as e:
        raise Problem(502, "Engine Error", f"Extraction engine unreachable ({e.__class__.__name__}).") from e

    if resp.status_code != 200:
        # Keep upstream detail (and the key) server-side; expose only the status code.
        raise Problem(502, "Engine Error", f"Extraction engine returned HTTP {resp.status_code}.")

    try:
        data = resp.json()
        parsed = json.loads(data["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, ValueError) as e:
        raise Problem(502, "Engine Error", "Extraction engine returned an unreadable response.") from e
    if not isinstance(parsed, dict):
        raise Problem(502, "Engine Error", "Extraction engine returned unexpected JSON.")
    return parsed
