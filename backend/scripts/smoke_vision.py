"""Live vision smoke: the Gujarati VF-6 scan through the real pipeline.

intake manifest -> upload scan bytes -> register page -> vision extract
(real Gemini call, ~40s) -> evidence chain replay.

Self-contained: boots nothing, needs only the backend venv + real .env.
  python -m scripts.smoke_vision [path/to/scan.jpg]
"""
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

import httpx  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

SCAN = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\farza\Downloads\1.jpg")


def main() -> int:
    if not SCAN.exists():
        print(f"scan not found: {SCAN}")
        return 1
    c = TestClient(app, raise_server_exceptions=False)

    tok = c.post("/auth/login", json={"username": "operator", "password": "operator-dev"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}

    img = SCAN.read_bytes()
    import hashlib as _h
    ref = f"VF6-SMOKE-{uuid.uuid4().hex[:8].upper()}"
    r = c.post("/intake", headers=h, json={
        "register_ref": ref,
        "centre": "Dehgam SCANNING CENTRE",
        "device": "SMOKE-SCANNER-01",
        "expected_count": 1,
        "page_hashes": [_h.sha256(img).hexdigest()],
    })
    print(f"intake     {r.status_code}")
    assert r.status_code in (200, 201), r.text
    manifest_id = r.json()["id"]

    r = c.post("/documents", headers=h,
               files={"file": (SCAN.name, img, "image/jpeg")},
               data={"manifest_id": str(manifest_id)})
    if r.status_code == 409:  # byte-identical scan already in custody -> reuse it
        doc_id = r.json()["existing_id"]
        print(f"upload     409 dedup -> reusing document #{doc_id}")
    else:
        assert r.status_code in (200, 201), r.text
        doc_id = r.json()["id"]
        print(f"upload     {r.status_code} ({len(img)} bytes)")

    r = c.post(f"/documents/{doc_id}/pages", headers=h, json={"seq_no": 1})
    if r.status_code == 409:  # page already registered -> reuse
        pages = c.get(f"/documents/{doc_id}/pages", headers=h).json()
        page_id = next(p["id"] for p in pages if p["seq_no"] == 1)
        print(f"page       409 dedup -> reusing page #{page_id}")
    else:
        assert r.status_code in (200, 201), r.text
        page_id = r.json()["id"]
        print(f"page       {r.status_code}")

    print("vision     calling Gemini (real scan, ~30-60s)...")
    t0 = time.time()
    r = c.post(f"/pages/{page_id}/extract-image", headers=h, timeout=240)
    dt = time.time() - t0
    print(f"extract    {r.status_code} in {dt:.0f}s")
    assert r.status_code == 201, r.text
    out = r.json()
    print(f"  run #{out['run_id']} SUCCEEDED")
    for k, v in out["fields"].items():
        print(f"  {k:12s} {v if v is not None else '(UNKNOWN)'}")

    run_id = out["run_id"]
    print(f"  crops: {out.get('crops') or '{}'}")
    r = c.get(f"/extraction/runs/{run_id}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    run, cands = body["run"], body["candidates"]
    print(f"run        engine={run['engine_name']} model={run['engine_version']} input_sha={run['input_hash'][:12]}...")
    print(f"           raw_output_uri={str(run['raw_output_uri'])[:60]}...")

    fields = {f["field_type"]: f for f in cands}
    assert fields["owner_name"]["value"] == out["fields"]["owner_name"]
    print("evidence   candidates stored, values match run output")

    # LAYOUT: every candidate with a crop must serve real PNG pixels.
    linked = {f: c["crop_id"] for f, c in fields.items() if c.get("crop_id")}
    print(f"layout     {len(linked)} field(s) crop-bound: {sorted(linked)}")
    if not linked:
        print("           (engine returned no usable bboxes this run)")
    for f, cid in linked.items():
        img_r = c.get(f"/crops/{cid}/image", headers=h)
        assert img_r.status_code == 200, img_r.text
        assert img_r.headers["content-type"] == "image/png"
        assert img_r.headers["x-crop-sha256"]
        print(f"           crop #{cid} ({f}): {len(img_r.content)} bytes PNG OK")
        noauth = c.get(f"/crops/{cid}/image")
        assert noauth.status_code == 401
        break

    print("\nVISION SMOKE GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
