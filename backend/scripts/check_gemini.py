"""Verify the Gemini engine works:  python -m scripts.check_gemini

1. Lists models visible to the key (count + flash/pro families).
2. Runs one structured-output generateContent call (schema-locked JSON,
   the exact mode the extraction engine uses) and prints the parsed result.
Exit 0 = key + model + structured mode all working.
"""
import json
import sys
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console: print Devanagari safely

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings  # noqa: E402

BASE = "https://generativelanguage.googleapis.com/v1beta"

# Synthetic mini-record: Devanagari digits + Hinglish, the real target domain.
SMOKE_PROMPT = (
    "You are reading a land-record register line. Extract the fields. "
    "Khasra number is २३४, owner name is राम प्रसाद, area is २-४० bigha. "
    "If a value is unreadable, return null for it instead of guessing."
)
SMOKE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "khasra_no": {"type": "STRING", "nullable": True},
        "owner_name": {"type": "STRING", "nullable": True},
        "area_raw": {"type": "STRING", "nullable": True},
    },
    "required": ["khasra_no", "owner_name", "area_raw"],
}


def main() -> int:
    s = get_settings()
    if not s.gemini_api_key:
        print("GEMINI_API_KEY not set in .env")
        return 1
    headers = {"x-goog-api-key": s.gemini_api_key}

    # 1. Key validity + model visibility
    with httpx.Client(timeout=30) as client:
        resp = client.get(f"{BASE}/models", headers=headers)
        resp.raise_for_status()
        names = sorted(m["name"].split("/")[-1] for m in resp.json().get("models", []))
        families = [n for n in names if "flash" in n or "pro" in n]
        print(f"key OK — {len(names)} models visible, {len(families)} generate-capable")

        # 2. Structured smoke call on the configured model
        body = {
            "contents": [{"parts": [{"text": SMOKE_PROMPT}]}],
            "generationConfig": {
                "temperature": 0,
                "response_mime_type": "application/json",
                "response_schema": SMOKE_SCHEMA,
            },
        }
        resp = client.post(
            f"{BASE}/models/{s.gemini_model}:generateContent",
            headers={**headers, "Content-Type": "application/json"},
            json=body,
        )
        if resp.status_code != 200:
            print(f"smoke call failed: HTTP {resp.status_code}: {resp.text[:300]}")
            return 1
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
        print(f"model {s.gemini_model} structured call OK:")
        for k, v in parsed.items():
            print(f"  {k} = {v!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
