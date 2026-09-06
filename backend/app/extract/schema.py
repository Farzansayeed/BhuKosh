"""Shared extraction target: land-record fields (PS #9 full field set).

CORE fields drive the workflow (routing, projection identity, validation);
EXTENDED fields (PS #9: khata, tehsil, district, classification, ownership,
mutation, registration, survey) are captured when the document carries them
and never block the record on absence.

Both the /extract prototype route and the evidence-bound processing runs use
this exact prompt + schema, so results stay comparable across paths.
"""

CORE_FIELDS = ("khasra_no", "owner_name", "area_raw", "village")
EXTENDED_FIELDS = (
    "khata_no",
    "survey_no",
    "tehsil",
    "district",
    "land_classification",
    "ownership_type",
    "mutation_ref",
    "registration_ref",
)
ALL_FIELDS = CORE_FIELDS + EXTENDED_FIELDS

KHASRA_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        name: {"type": "STRING", "nullable": True} for name in ALL_FIELDS
    },
    "required": ["khasra_no", "owner_name", "area_raw"],
}

# Confidence self-report: one 0..1 score per field (PS #11). Nullable — the
# engine may omit a score exactly where it omitted a value.
FIELD_CONFIDENCE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        f"{name}_confidence": {
            "type": "NUMBER",
            "nullable": True,
            "description": "0.0-1.0 confidence that the extracted value is read correctly",
        }
        for name in ALL_FIELDS
    },
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
        for name in ALL_FIELDS
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
    "- khata_no: the khata/khewat account number (खाता नंबर)\n"
    "- survey_no: the survey number when distinct from khasra (सर्वे नंबर)\n"
    "- tehsil: the tehsil/mandal/block (तहसील/मंडल)\n"
    "- district: the district/zilla (जिला)\n"
    "- land_classification: land use/type (भूमि का प्रकार — कृषि/अकृषि/गैर-कृषि, e.g. 'समतल चिरायत')\n"
    "- ownership_type: ownership form (स्वत्व — bhumidhar/sirdar/govt/tenant, or as printed)\n"
    "- mutation_ref: mutation/inheritance entry reference when present (नामांतरण/वंशावली संदर्भ)\n"
    "- registration_ref: registration/document reference when present (पंजीकरण संदर्भ)\n"
    "The document may be from ANY Indian state and in ANY Indic script "
    "(Devanagari/Hindi, Gujarati, Gurmukhi, etc.) — register lines or printed "
    "government forms (jamabandi, khatauni, VF-6/VF-7/village form entries). "
    "Take numbers/names from the field the form designates for them; for survey/"
    "khasra identifiers prefer the record's designated number field, falling "
    "back to the printed form footer only when the body has none. "
    "Keep each value in its printed script (Devanagari stays Devanagari, "
    "Gujarati stays Gujarati). "
    "For each field also report {field}_confidence: your 0.0-1.0 confidence "
    "that the value is read correctly (>=0.9 clearly printed and unambiguous; "
    "<0.6 faded, ambiguous, overlapping, or guessed). "
    "If a field is unreadable or absent, return null for it instead of guessing."
)

VISION_ANNOTATE = (
    "After extracting each field, report the pixel region it was read from as a "
    "tight bounding box [x1, y1, x2, y2] with all coordinates normalized to a "
    "0..1000 scale relative to the full image (regardless of true pixel size). "
    "The box must cover exactly the printed value, not the whole cell or row. "
    "If a field is null, its bbox must also be null."
)
