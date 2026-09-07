"""Reproduce the user's upload bug: two DIFFERENT PDFs -> second upload 409?
Also simulates the fixed UI resume path: 409 -> reuse stored doc/page -> extract.
"""
import hashlib
import uuid

import httpx

RUN = uuid.uuid4().hex[:6]  # unique intake refs per script run (the UI appends Date.now())

BASE = "http://127.0.0.1:8000"


def make_pdf(num: int) -> bytes:
    """Two minimal but genuinely different single-page PDFs."""
    content = f"BT /F1 12 Tf 72 700 Td (Register page {num} - village Salempr khasra {100 + num}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF"
    )
    return bytes(out)


def main():
    c = httpx.Client(timeout=30)
    # 1. login
    r = c.post(f"{BASE}/auth/login", json={"username": "operator", "password": "operator-dev"})
    r.raise_for_status()
    tok = r.json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}

    pdf_a, pdf_b = make_pdf(1), make_pdf(2)
    print(f"pdf_a sha256 {hashlib.sha256(pdf_a).hexdigest()[:16]}  ({len(pdf_a)} bytes)")
    print(f"pdf_b sha256 {hashlib.sha256(pdf_b).hexdigest()[:16]}  ({len(pdf_b)} bytes)")
    assert pdf_a != pdf_b, "PDFs must differ"

    def intake(label):
        r = c.post(f"{BASE}/intake", headers=h, json={
            "register_ref": f"REPRO-{RUN}-{label[0]}-{label[-1]}", "centre": "repro",
            "device": "repro", "expected_count": 1,
        })
        r.raise_for_status()
        return r.json()["id"]

    def upload(mid, label, blob):
        return c.post(
            f"{BASE}/documents", headers=h,
            data={"manifest_id": str(mid)},
            files={"file": (f"repro_{label[0]}.pdf", blob, "application/pdf")},
        )

    for i, (label, blob) in enumerate((("A (first upload)", pdf_a), ("B (different bytes)", pdf_b),
                        ("A again (same bytes renamed)", pdf_a))):
        mid = intake(f"{label} {i}")
        r = upload(mid, label, blob)
        print(f"\nupload {label}: HTTP {r.status_code}")
        if r.status_code == 201:
            d = r.json()
            print(f"  document id={d['id']} sha={d['sha256'][:16]} mime={d['mime']} size={d['size_bytes']}")
        else:
            print(f"  body: {r.text[:300]}")

    # 2. RESUME PATH (what the fixed Upload.jsx now does on 409):
    #    same file again -> 409 with existing_id -> GET that document ->
    #    page 409 -> GET pages -> reuse page 1. No dead end, no duplicate.
    print("\n=== resume-path simulation (fixed UI behavior) ===")
    mid = intake("A (first upload)")
    r = upload(mid, "A (first upload)", pdf_a)
    assert r.status_code == 409, f"expected 409 (doc stored earlier this run), got {r.status_code}"
    problem = r.json()
    existing_id = problem.get("existing_id")
    assert existing_id, f"409 body missing existing_id: {problem}"
    print(f"409 -> existing_id={existing_id}  title={problem.get('title')!r}")

    r = c.get(f"{BASE}/documents/{existing_id}", headers=h)
    r.raise_for_status()
    d = r.json()
    print(f"resume doc: id={d['id']} sha={d['sha256'][:16]} filename={d['original_filename']}")

    r = c.post(f"{BASE}/documents/{d['id']}/pages", headers=h, json={"seq_no": 1, "sha256": d["sha256"]})
    if r.status_code == 409:
        r2 = c.get(f"{BASE}/documents/{d['id']}/pages", headers=h)
        r2.raise_for_status()
        pages = r2.json()
        p1 = next((pg for pg in pages if pg["seq_no"] == 1), None)
        assert p1, "no page 1 found on resumed document"
        print(f"page 409 -> reuse page id={p1['id']} seq_no={p1['seq_no']}")
    else:
        r.raise_for_status()
        p1 = r.json()
        print(f"page registered fresh: id={p1['id']}")
    print("RESUME OK — extraction can proceed on the stored copy")


if __name__ == "__main__":
    main()
