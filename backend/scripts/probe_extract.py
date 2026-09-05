"""Isolation probe: server prompt + payload -> Gemini directly (no uvicorn)."""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.extract.gemini_client import structured_extract  # noqa: E402
from app.extract.router import KHASRA_SCHEMA, PROMPT  # noqa: E402

PAYLOAD = "खसरा नंबर २३४, गाँव सलेमपुर, रकबा २-४० bigha, किसान का नाम राम प्रसाद पुत्र श्याम लाल"

print("PROMPT as server builds it:")
print("-" * 60)
full = f"{PROMPT}\n\nRECORD TEXT:\n{PAYLOAD}"
print(full)
print("-" * 60)
print("payload codepoints:", len(PAYLOAD))
result = structured_extract(full, KHASRA_SCHEMA)
print("result:", result)
