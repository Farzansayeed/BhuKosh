"""Seed a demo dataset into a running BhuKosh API (default: production).

python -m scripts.seed_demo [BASE_URL]

Creates DEMO-* manifests with 8 records across villages/scripts, then drives
each to a different pipeline state so the dashboard shows a real spread:
VALIDATED, REJECTED, OFFICER_CERTIFIED, REVIEW_REQUIRED x2, EXTRACTED,
VERIFIED. Records 1 and 5 share village+khasra with wildly different areas,
so validating #5 trips the cross-record R-AREA-JUMP anomaly.

Extraction calls are REAL engine calls, paced to respect the per-user rate
limit (5/min default -> 13s sleep). Read-only for existing data; uses the
public API exactly as a user would.
"""
import hashlib
import io
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://bhukosh-api.vercel.app"
PACE_SECONDS = 13  # rate limit: 5/min per user

# (label, register text, target pipeline state)
PLAN = [
    ("clean-up", "खसरा नंबर २३४, गाँव सलेमपुर, रकबा २-४० bigha, किसान का नाम राम प्रसाद पुत्र श्याम लाल", "VALIDATED"),
    ("reject-me", "खसरा नंबर २३५, गाँव सलेमपुर, रकबा ३-१० bigha, किसान का नाम सुरेश कुमार पुत्र मोहन", "REJECTED"),
    ("gujarat", "ખેતર નં. ૫૫૯, ગામ ઓઢવ, વિસ્તાર ૪૧.૭૬ હેક્ટર, ખેતુકનું નામ ભુલાજી ખોડાજી", "OFFICER_CERTIFIED"),
    ("no-owner", "खसरा नंबर ५६७, गाँव सलेमपुर, रकबा १-५ bigha", "REVIEW_REQUIRED"),
    ("area-jump", "खसरा नंबर २३४, गाँव सलेमपुर, रकबा ७-८० bigha, किसान का नाम राम प्रसाद पुत्र श्याम लाल (बंटोबस्ती उपरांत)", "REVIEW_REQUIRED"),
    ("devariya", "खसरा नंबर ११२, गाँव देवरिया, रकबा ४-१५ bigha, किसान का नाम अमित वर्मा पुत्र राजेश वर्मा", "VALIDATED"),
    ("bhagwanpur", "खसरा नंबर ७८, गाँव भगवानपुर, रकबा २-० bigha, किसान का नाम प्रीति सिंह", "EXTRACTED"),
    ("verify-me", "खसरा नंबर ७९, गाँव भगवानपुर, रकबा ५-३० bigha, किसान का नाम विजय यादव पुत्र गोपाल यादव", "VERIFIED"),
]

UNIQ = str(time.time_ns())
_last_extract = 0.0


def login(c: httpx.Client, username: str, password: str) -> dict:
    tok = c.post("/auth/login", json={"username": username, "password": password}
                 ).raise_for_status().json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def make_record(c: httpx.Client, h: dict, label: str, text: str) -> dict:
    """intake -> upload -> page -> extract -> project. Returns projection body."""
    global _last_extract
    wait = PACE_SECONDS - (time.time() - _last_extract)
    if wait > 0:
        print(f"    (pacing {wait:.0f}s for rate limit)")
        time.sleep(wait)

    png = b"\x89PNG\r\n\x1a\n" + hashlib.sha256(f"{UNIQ}-{label}".encode()).digest()
    m = c.post("/intake", headers=h, json={
        "register_ref": f"DEMO-{UNIQ}-{label}", "centre": "Demo centre",
        "device": "seed-01", "expected_count": 1,
        "page_hashes": [hashlib.sha256(png).hexdigest()],
    }).raise_for_status().json()
    doc = c.post("/documents", headers=h, data={"manifest_id": str(m["id"])},
                 files={"file": (f"{label}.png", io.BytesIO(png), "image/png")},
                 ).raise_for_status().json()
    page = c.post(f"/documents/{doc['id']}/pages", headers=h,
                  json={"seq_no": 1, "sha256": doc["sha256"], "width": 1, "height": 1},
                  ).raise_for_status().json()
    _last_extract = time.time()
    r = c.post(f"/documents/{doc['id']}/extract", headers=h,
               json={"text": text, "page_id": page["id"]})
    if r.status_code != 201:
        raise SystemExit(f"extract failed for {label}: {r.status_code} {r.text[:300]}")
    run_id = r.json()["run"]["id"]
    print(f"    run #{run_id} -> projecting")
    proj = c.post(f"/records/from-run/{run_id}", headers=h)
    if proj.status_code not in (200, 201):
        raise SystemExit(f"projection failed for {label}: {proj.status_code} {proj.text[:300]}")
    return proj.json()


def _record_view(c: httpx.Client, h: dict, rec_id: int) -> dict:
    body = c.get(f"/records/{rec_id}", headers=h).raise_for_status().json()
    return body.get("record", body)


def decide(c: httpx.Client, h: dict, rec_id: int, kind: str, reason: str | None = None) -> dict:
    """Decision with a freshly-read expected_version (the API is 409-strict)."""
    ver = _record_view(c, h, rec_id)["record_version"]
    body = {"decision_type": kind, "expected_version": ver}
    if reason:
        body["reason"] = reason
    r = c.post(f"/records/{rec_id}/decisions", headers=h, json=body)
    if r.status_code != 200:
        raise SystemExit(f"{kind} failed on #{rec_id}: {r.status_code} {r.text[:300]}")
    return r.json()["record"]


def current_state(c: httpx.Client, h: dict, rec_id: int) -> str:
    return _record_view(c, h, rec_id)["current_state"]


def drive_to(c: httpx.Client, rec: dict, target: str, checker: dict, certifier: dict) -> str:
    rid = rec["record"]["id"]

    def validate() -> None:
        r = c.post(f"/records/{rid}/validate", headers=checker)
        if r.status_code != 200:
            raise SystemExit(f"validate failed on #{rid}: {r.status_code} {r.text[:300]}")

    if target == "EXTRACTED":
        return current_state(c, checker, rid)
    if target in ("VALIDATED", "REVIEW_REQUIRED"):
        validate()
        return current_state(c, checker, rid)
    if target == "REJECTED":
        validate()
        return decide(c, checker, rid, "REJECT", "demo: rejected at review")["current_state"]

    # VERIFIED / OFFICER_CERTIFIED: APPROVE is only legal from REVIEW_REQUIRED.
    # Clean records get there the honest way: REJECT -> REOPEN (the "bounced
    # once, then verified" workflow), which doubles as a demo of the chain.
    validate()
    if current_state(c, checker, rid) != "REVIEW_REQUIRED":
        decide(c, checker, rid, "REJECT", "demo: bounced for re-check")
        decide(c, checker, rid, "REOPEN", "demo: recheck complete")
    rec2 = decide(c, checker, rid, "APPROVE")
    if target == "VERIFIED":
        return rec2["current_state"]
    return decide(c, certifier, rid, "CERTIFY")["current_state"]


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=180)
    operator = login(c, "operator", "operator-dev")
    checker = login(c, "checker", "checker-dev")
    certifier = login(c, "certifier", "certifier-dev")

    print(f"seeding {len(PLAN)} demo records into {BASE}")
    results = []
    for i, (label, text, target) in enumerate(PLAN, 1):
        print(f"[{i}/{len(PLAN)}] {label} -> {target}")
        rec = make_record(c, operator, label, text)
        state = drive_to(c, rec, target, checker, certifier)
        results.append((label, rec["record"]["id"], state))
        print(f"    record #{rec['record']['id']} = {state}")

    print("\n=== seeded ===")
    for label, rid, state in results:
        print(f"  #{rid:<4} {state:<18} {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
