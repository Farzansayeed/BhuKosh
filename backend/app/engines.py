"""Multi-engine registry — engine-agnostic extraction (PS #7+ scalability).

BhuKosh is not married to one AI provider. Each engine implements the same
structured-output contract; every run records which engine + model produced
it (evidence-bound), and uploaders choose the engine per job, informed by
each one's strengths.

Engines:
  gemini     — Google Gemini (structured output via response_schema; best free
               accuracy on handwritten Indic scripts; daily free quota resets
               midnight Pacific).
  openrouter — OpenRouter (one key, many free models; minimax-m3:free chosen
               after a live bake-off on a real Gujarati scan — vision+text,
               ~4s round-trip).
  groq       — Groq (OpenAI-compatible; free tier is now TEXT-only — Qwen/GPT-OSS
               models; fast, generous; good for bulk printed-text extraction).

Both are server-held keys: configured via env, never sent to or logged for
clients. A missing key disables the engine (it still appears in the catalog
marked unavailable, so the UI can explain why).
"""
import base64
import json
import time

import httpx

from .config import get_settings
from .errors import Problem

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GROQ_BASE = "https://api.groq.com/openai/v1"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Headers every OpenRouter call should carry (attribution per their docs).
def _openrouter_headers(s) -> dict:
    return {
        "Authorization": f"Bearer {s.openrouter_api_key}",
        "Content-Type": "application/json",
        "User-Agent": "BhuKosh/1.0",
    }
# Catalog shown to uploaders: strengths are honest, not marketing.
CATALOG = [
    {
        "id": "gemini",
        "label": "Google Gemini",
        "model_env": "gemini_model",
        "strengths": [
            "Best free accuracy on HANDWRITTEN Indic scripts (khatauni, VF-6 scans)",
            "Reads complex register layouts and faded scans well",
            "Native structured output; evidence bboxes for pixel highlights",
        ],
        "weaknesses": ["Daily free quota resets midnight Pacific — bursts can 429"],
        "best_for": "Handwritten scans, difficult documents, vision+crop extraction",
    },
    {
        "id": "openrouter",
        "label": "OpenRouter (MiniMax M3)",
        "model_env": "openrouter_model",
        "strengths": [
            "Vision + text on free models — verified reading Gujarati scans",
            "Independent quota from Gemini — the 429 safety net",
            "One key, many models: easy to swap as free-model landscape shifts",
        ],
        "weaknesses": ["Free models are rate-limited and can be busy at peak times"],
        "best_for": "Fallback for scans when Gemini quota is out; independent second opinion",
    },
    {
        "id": "groq",
        "label": "Groq (Qwen)",
        "model_env": "groq_model",
        "strengths": [
            "Extremely fast inference (often 3-10x quicker round-trip)",
            "Generous free tier — good for bulk text-line extraction",
            "Strong on PRINTED text and clean forms",
        ],
        "weaknesses": [
            "Free tier has NO vision models — text mode only",
            "Weaker on handwriting than Gemini",
        ],
        "best_for": "Bulk printed-register text lines at high speed",
    },
]


def _settings():
    return get_settings()


# ---------- Gemini (delegates to the proven extract client — one source of
# truth for the HTTP logic, and test stubs on gemini_client keep working) ----------

from .extract import gemini_client  # noqa: E402 — deliberate late import


def gemini_structured_text(prompt: str, schema: dict, timeout: float = 120.0) -> dict:
    return gemini_client.structured_extract(prompt, schema, timeout=timeout)


def gemini_structured_image(image_bytes: bytes, mime: str, schema: dict, prompt: str,
                            timeout: float = 180.0) -> dict:
    return gemini_client.structured_extract_image(image_bytes, mime, schema, prompt=prompt, timeout=timeout)


# ---------- Groq (OpenAI-compatible chat completions) ----------

_GROQ_JSON_HINT = (
    "Respond with ONLY a JSON object matching this schema (no markdown, no "
    "commentary). Types: OBJECT=object, ARRAY=array, STRING=string, "
    "NUMBER=number, BOOLEAN=boolean. Use null for missing/nullable values.\n"
    "SCHEMA:\n"
)


def _groq_call(prompt: str, schema: dict, image_b64: str | None, mime: str | None,
               timeout: float) -> dict:
    s = _settings()
    if not s.groq_api_key:
        raise Problem(503, "Engine Unavailable", "Groq is not configured on this server.")

    content: list | str
    if image_b64:
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
        ]
    else:
        content = prompt

    body = {
        "model": s.groq_model,
        "messages": [{
            "role": "user",
            "content": content,
        }],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "max_tokens": 4096,
    }
    full_prompt = _GROQ_JSON_HINT + json.dumps(schema, ensure_ascii=False) + "\n\n" + (
        prompt if isinstance(content, str) else prompt
    )
    body["messages"][0]["content"] = (
        [{"type": "text", "text": full_prompt}] + content[1:]
        if isinstance(content, list) else full_prompt
    )

    last_code = 0
    resp = None
    for attempt in range(3):
        try:
            resp = httpx.post(
                f"{GROQ_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {s.groq_api_key}", "Content-Type": "application/json"},
                json=body,
                timeout=timeout,
            )
        except httpx.HTTPError as e:
            raise Problem(502, "Engine Error", f"Groq unreachable ({e.__class__.__name__}).") from e
        last_code = resp.status_code
        if resp.status_code in (429, 503) and attempt < 2:
            time.sleep(2.0 * (attempt + 1))
            continue
        break
    if resp.status_code != 200:
        raise Problem(502, "Engine Error", f"Groq returned HTTP {resp.status_code}.")
    try:
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        # tolerate accidental code fences
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        parsed = json.loads(text)
    except (KeyError, IndexError, ValueError) as e:
        raise Problem(502, "Engine Error", "Groq returned an unreadable response.") from e
    if not isinstance(parsed, dict):
        raise Problem(502, "Engine Error", "Groq returned unexpected JSON.")
    return parsed


def groq_structured_text(prompt: str, schema: dict, timeout: float = 120.0) -> dict:
    return _groq_call(prompt, schema, None, None, timeout)


def groq_structured_image(image_bytes: bytes, mime: str, schema: dict, prompt: str,
                          timeout: float = 180.0) -> dict:
    """Kept for the contract, but Groq's free tier has no vision models —
    this raises 503 Engine Unavailable (honest, visible in the catalog)."""
    s = _settings()
    raise Problem(503, "Engine Unavailable",
                  "Groq's free tier offers no vision-capable models; use it in text mode only.")


# ---------- OpenRouter (OpenAI-compatible; free models on one key) ----------

def _openrouter_call(prompt: str, schema: dict, image_b64: str | None, mime: str | None,
                     timeout: float) -> dict:
    s = _settings()
    if not s.openrouter_api_key:
        raise Problem(503, "Engine Unavailable", "OpenRouter is not configured on this server.")

    content: list | str
    if image_b64:
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
        ]
    else:
        content = prompt

    full_prompt = _GROQ_JSON_HINT + json.dumps(schema, ensure_ascii=False) + "\n\n" + (
        prompt if isinstance(content, str) else prompt
    )
    body = {
        "model": s.openrouter_model,
        "messages": [{
            "role": "user",
            "content": (
                [{"type": "text", "text": full_prompt}] + content[1:]
                if isinstance(content, list) else full_prompt
            ),
        }],
        "temperature": 0,
        "max_tokens": 4096,
    }

    resp = None
    for attempt in range(3):
        try:
            resp = httpx.post(
                f"{OPENROUTER_BASE}/chat/completions",
                headers=_openrouter_headers(s),
                json=body,
                timeout=timeout,
            )
        except httpx.HTTPError as e:
            raise Problem(502, "Engine Error", f"OpenRouter unreachable ({e.__class__.__name__}).") from e
        if resp.status_code in (429, 503) and attempt < 2:
            time.sleep(2.0 * (attempt + 1))
            continue
        break
    if resp.status_code != 200:
        raise Problem(502, "Engine Error", f"OpenRouter returned HTTP {resp.status_code}.")
    try:
        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        parsed = json.loads(text)
    except (KeyError, IndexError, ValueError) as e:
        raise Problem(502, "Engine Error", "OpenRouter returned an unreadable response.") from e
    if not isinstance(parsed, dict):
        raise Problem(502, "Engine Error", "OpenRouter returned unexpected JSON.")
    return parsed


def openrouter_structured_text(prompt: str, schema: dict, timeout: float = 120.0) -> dict:
    return _openrouter_call(prompt, schema, None, None, timeout)


def openrouter_structured_image(image_bytes: bytes, mime: str, schema: dict, prompt: str,
                                timeout: float = 180.0) -> dict:
    b64 = base64.b64encode(image_bytes).decode()
    return _openrouter_call(prompt, schema, b64, mime, timeout)


# ---------- Unified dispatch ----------

_ENGINES = {
    "gemini": {
        "text": gemini_structured_text,
        "image": gemini_structured_image,
        "model": lambda: _settings().gemini_model,
        "available": lambda: bool(_settings().gemini_api_key),
    },
    "openrouter": {
        "text": openrouter_structured_text,
        "image": openrouter_structured_image,
        "model": lambda: _settings().openrouter_model,
        "available": lambda: bool(_settings().openrouter_api_key),
    },
    "groq": {
        "text": groq_structured_text,
        "image": groq_structured_image,
        "model": lambda: _settings().groq_model,
        "available": lambda: bool(_settings().groq_api_key),
    },
}


def engine_available(engine_id: str) -> bool:
    eng = _ENGINES.get(engine_id)
    return bool(eng and eng["available"]())


def run_structured(engine_id: str, mode: str, *, prompt: str, schema: dict,
                   image_bytes: bytes | None = None, mime: str | None = None,
                   timeout: float = 180.0) -> tuple[dict, str]:
    """Execute one structured call on the chosen engine.

    Returns (parsed_dict, model_version). Raises Problem for unknown or
    unavailable engines — the caller records the FAILED run as usual.
    """
    eng = _ENGINES.get(engine_id)
    if not eng:
        raise Problem(422, "Validation Failed",
                      f"Unknown engine '{engine_id}'. Available: {sorted(_ENGINES)}.")
    if not eng["available"]():
        raise Problem(503, "Engine Unavailable",
                      f"Engine '{engine_id}' is not configured on this server.")
    if mode == "image":
        out = eng["image"](image_bytes, mime, schema, prompt, timeout=timeout)
    else:
        out = eng["text"](prompt, schema, timeout=timeout)
    return out, eng["model"]()


def catalog() -> list[dict]:
    """Catalog with live availability — the UI shows only usable engines."""
    s = _settings()
    out = []
    for c in CATALOG:
        out.append({
            **c,
            "model": getattr(s, c["model_env"]),
            "available": engine_available(c["id"]),
        })
    return out
