"""Live PROCESSING smoke:  python -m scripts.smoke_processing

In-process uvicorn (port 8002, daemon thread — same pattern as smoke_custody),
one REAL Gemini call: login → intake manifest → upload page → extraction run →
candidates printed. Creates one SMOKE-* manifest (kept on purpose).

Page bytes and manifest ref are time-unique per run: documents.sha256 and
manifest_hash are globally unique, so fixed bytes would collide (409).
"""
import hashlib
import io
import sys
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from app.main import app  # noqa: E402

HOST, PORT = "127.0.0.1", 8002
UNIQ = str(time.time_ns())
PNG = b"\x89PNG\r\n\x1a\n" + hashlib.sha256(UNIQ.encode()).digest()
REGISTER_TEXT = "खसरा नंबर २३४, गाँव सलेमपुर, रकबा २-४० bigha, किसान का नाम राम प्रसाद पुत्र श्याम लाल"


def main() -> int:
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()

    base = f"http://{HOST}:{PORT}"
    for _ in range(50):
        try:
            if httpx.get(f"{base}/health", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        print("server never came up")
        return 1

    c = httpx.Client(base_url=base, timeout=120)
    tok = c.post("/auth/login", json={"username": "operator", "password": "operator-dev"}
                 ).raise_for_status().json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}

    m = c.post("/intake", headers=h, json={
        "register_ref": f"SMOKE-{UNIQ}",
        "centre": "Tehsil Salempr",
        "device": "scan-01",
        "expected_count": 1,
        "page_hashes": [hashlib.sha256(PNG).hexdigest()],
    }).raise_for_status().json()
    doc = c.post("/documents", headers=h,
                 data={"manifest_id": str(m["id"])},
                 files={"file": ("smoke-page-1.png", io.BytesIO(PNG), "image/png")},
                 ).raise_for_status().json()
    page = c.post(f"/documents/{doc['id']}/pages", headers=h,
                  json={"seq_no": 1, "sha256": doc["sha256"], "width": 1, "height": 1},
                  ).raise_for_status().json()
    print("manifest:", m["id"], "| document:", doc["id"], "| page:", page["id"])

    r = c.post(f"/documents/{doc['id']}/extract", headers=h,
               json={"text": REGISTER_TEXT, "page_id": page["id"]})
    if r.status_code != 201:
        print("extract failed:", r.status_code, r.text[:300])
        server.should_exit = True
        return 1
    payload = r.json()
    run, cands = payload["run"], payload["candidates"]
    print(f"run {run['id']}: status={run['status']} engine={run['engine_name']}/{run['engine_version']}")
    for cd in cands:
        v = "UNKNOWN" if cd["is_unknown"] else repr(cd["value"])
        print(f"  {cd['field_type']:<11} = {v}")
    print("raw output preserved at:", run["raw_output_uri"])
    server.should_exit = True
    return 0 if run["status"] == "SUCCEEDED" and cands else 1


if __name__ == "__main__":
    raise SystemExit(main())
