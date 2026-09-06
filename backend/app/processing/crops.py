"""LAYOUT-stage crop service: region-bound evidence from field bounding boxes.

The vision engine returns per-field bboxes normalized to 0..1000 (resolution-
independent). This module materializes each box as a real image crop with
padding, stores it content-addressed via the standard storage layer, and
records it in evidence_crops — so a displayed value can show the exact
pixels it was read from (plan §3A: every value one click from its evidence).
"""
from app.storage import open_uri, save_document, sha256_bytes
from PIL import Image
import io

PAD_FRACTION = 0.004  # 0.4% of the larger dimension — enough to include glyph edges


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def crop_image(page_bytes: bytes, bbox_norm: list[int]) -> tuple[bytes, str, list[int]]:
    """Cut one crop; returns (png_bytes, sha256, absolute_pixel_bbox [x1,y1,x2,y2])."""
    if not isinstance(bbox_norm, list) or len(bbox_norm) != 4 or not all(
        isinstance(v, int) and 0 <= v <= 1000 for v in bbox_norm
    ):
        raise ValueError("bbox must be [x1, y1, x2, y2] with ints in 0..1000")
    x1n, y1n, x2n, y2n = bbox_norm
    if x2n <= x1n or y2n <= y1n:
        raise ValueError("bbox is empty (x2<=x1 or y2<=y1)")

    img = Image.open(io.BytesIO(page_bytes))
    W, H = img.size
    pad = int(PAD_FRACTION * max(W, H))
    x1 = _clamp(round(x1n / 1000 * W) - pad, 0, W)
    y1 = _clamp(round(y1n / 1000 * H) - pad, 0, H)
    x2 = _clamp(round(x2n / 1000 * W) + pad, 0, W)
    y2 = _clamp(round(y2n / 1000 * H) + pad, 0, H)

    region = img.crop((x1, y1, x2, y2))
    buf = io.BytesIO()
    region.save(buf, format="PNG")
    data = buf.getvalue()
    return data, sha256_bytes(data), [x1, y1, x2, y2]


def store_crop(page_id: int, page_storage_uri: str, bbox_norm: list[int]) -> dict:
    """Materialize + persist one crop row. Idempotent per (page, crop bytes)."""
    page_bytes = open_uri(page_storage_uri)
    try:
        png, digest, px_bbox = crop_image(page_bytes, bbox_norm)
    except OSError:  # PIL raises OSError subclasses for undecodable images
        raise ValueError("page bytes are not a decodable image") from None
    uri = save_document(png, digest)  # content-addressed; re-crop is a no-op
    return {"page_id": page_id, "bbox": px_bbox, "crop_hash": digest, "storage_uri": uri}
