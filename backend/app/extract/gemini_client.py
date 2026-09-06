"""Server-held Gemini client — the API key never leaves the backend process.

Callers get schema-locked JSON or an RFC-7807 problem; upstream response bodies
and credentials are never echoed to clients.
"""
import base64
import json
import time

import httpx

from ..config import get_settings
from ..errors import Problem

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def structured_extract(text: str, schema: dict, *, timeout: float = 120.0) -> dict:
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
    return _generate(body, timeout)


def structured_extract_image(image_bytes: bytes, mime_type: str, schema: dict, *, timeout: float = 180.0) -> dict:
    """Vision structured-output call: the scanned page itself is the input."""
    s = get_settings()
    if not s.gemini_api_key:
        raise Problem(503, "Engine Unavailable", "Extraction engine is not configured on this server.")

    body = {
        "contents": [{"parts": [
            {"text": "Extract from this scanned land-record document."},
            {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode()}},
        ]}],
        "generationConfig": {
            "temperature": 0,
            "response_mime_type": "application/json",
            "response_schema": schema,
        },
    }
    return _generate(body, timeout)


def _generate(body: dict, timeout: float, *, attempts: int = 3) -> dict:
    s = get_settings()
    last_code = 0
    for attempt in range(attempts):
        try:
            resp = httpx.post(
                f"{BASE_URL}/models/{s.gemini_model}:generateContent",
                headers={"x-goog-api-key": s.gemini_api_key, "Content-Type": "application/json"},
                json=body,
                timeout=timeout,
            )
        except httpx.HTTPError as e:
            raise Problem(502, "Engine Error", f"Extraction engine unreachable ({e.__class__.__name__}).") from e
        last_code = resp.status_code
        if resp.status_code in (429, 503) and attempt < attempts - 1:
            # Transient upstream overload/quota — back off and retry.
            time.sleep(2.0 * (attempt + 1))
            continue
        break

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
