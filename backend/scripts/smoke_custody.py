"""Live CUSTODY smoke:  python -m scripts.smoke_custody

Boots the real FastAPI app on a real uvicorn socket (in a daemon thread, port
8001 — no external server needed), then exercises login → intake manifest →
upload → idempotent re-upload (409) → page registration → manifest
verification → content roundtrip. Creates one SMOKE-* manifest in the dev
database (kept on purpose; cleanup is manual).
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

HOST, PORT = "127.0.0.1", 8001
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c636000010000000500010d0a2db40000000049454e44ae426082"
)


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

    c = httpx.Client(base_url=base, timeout=60)
    tok = c.post(
        "/auth/login", json={"username": "operator", "password": "operator-dev"}
    ).raise_for_status().json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}

    m = c.post("/intake", headers=h, json={
        "register_ref": "SMOKE-2026-001",
        "centre": "Tehsil Salempr",
        "device": "scan-01",
        "expected_count": 1,
        "page_hashes": [hashlib.sha256(PNG).hexdigest()],
    }).raise_for_status().json()
    print("manifest:", m["id"], "| hash:", m["manifest_hash"][:16], "…")

    doc = c.post("/documents", headers=h,
                 data={"manifest_id": str(m["id"])},
                 files={"file": ("smoke-page-1.png", io.BytesIO(PNG), "image/png")},
                 ).raise_for_status().json()
    print("document:", doc["id"], "| sha256:", doc["sha256"][:16], "… |", doc["size_bytes"], "bytes")

    dup = c.post("/documents", headers=h,
                 data={"manifest_id": str(m["id"])},
                 files={"file": ("smoke-page-1-again.png", io.BytesIO(PNG), "image/png")})
    print("re-upload ->", dup.status_code, "| existing_id:", dup.json().get("existing_id"))

    c.post(f"/documents/{doc['id']}/pages", headers=h,
           json={"seq_no": 1, "sha256": doc["sha256"], "width": 1, "height": 1},
           ).raise_for_status()
    man = c.get(f"/intake/{m['id']}", headers=h).raise_for_status().json()
    print("manifest verified_result:", man["verified_result"])

    back = c.get(f"/documents/{doc['id']}/content", headers=h)
    ok = back.status_code == 200 and back.content == PNG
    print("content roundtrip:", "OK" if ok else "FAIL")
    server.should_exit = True
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
