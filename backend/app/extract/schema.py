"""Shared extraction target: land-record register fields (prototype scope).

Both the /extract prototype route and the evidence-bound processing runs use
this exact prompt + schema, so results stay comparable across paths.
"""

KHASRA_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "khasra_no": {"type": "STRING", "nullable": True},
        "owner_name": {"type": "STRING", "nullable": True},
        "area_raw": {"type": "STRING", "nullable": True},
        "village": {"type": "STRING", "nullable": True},
    },
    "required": ["khasra_no", "owner_name", "area_raw"],
}

# Vision (LAYOUT) target: the same fields, each with the pixel bbox of the
# exact region it was read from — [x1, y1, x2, y2] normalized to 0..1000.
# The engine refines from full-page scanning to region-bound evidence.
FIELD_BBOXES_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        f"{name}_bbox": {
            "type": "ARRAY",
            "items": {"type": "INTEGER"},
            "nullable": True,
            "description": f"[x1, y1, x2, y2] of the '{name}' value, 0..1000 normalized",
        }
        for name in ("khasra_no", "owner_name", "area_raw", "village")
    },
}

PROMPT = (
    "You are reading a line/section from an Indian land-record register "
    "(khatauni/jamabandi), possibly handwritten or Hindi/English mixed. "
    "Extract exactly these fields from the record text below:\n"
    "- khasra_no: the khewat/khasra/gata number (खसरा/खेवट/गाता नंबर)\n"
    "- owner_name: the cultivator/owner name (किसान/स्वामी/खातेदार का नाम)\n"
    "- area_raw: the land area exactly as printed (रकबा/क्षेत्रफल, e.g. '२-४० bigha')\n"
    "- village: the village name (गाँव/मौजा)\n"
    "The document may be from ANY Indian state and in ANY Indic script "
    "(Devanagari/Hindi, Gujarati, Gurmukhi, etc.) — register lines or printed "
    "government forms (jamabandi, khatauni, VF-6/VF-7/village form entries). "
    "Take numbers/names from the field the form designates for them; for survey/"
    "khasra identifiers prefer the record's designated number field, falling "
    "back to the printed form footer only when the body has none. "
    "Keep each value in its printed script (Devanagari stays Devanagari, "
    "Gujarati stays Gujarati). "
    "If a field is unreadable or absent, return null for it instead of guessing."
)

VISION_ANNOTATE = (
    "After extracting each field, report the pixel region it was read from as a "
    "tight bounding box [x1, y1, x2, y2] with all coordinates normalized to a "
    "0..1000 scale relative to the full image (regardless of true pixel size). "
    "The box must cover exactly the printed value, not the whole cell or row. "
    "If a field is null, its bbox must also be null."
)
