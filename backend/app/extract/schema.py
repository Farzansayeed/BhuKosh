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

PROMPT = (
    "You are reading a line/section from an Indian land-record register "
    "(khatauni/jamabandi), possibly handwritten or Hindi/English mixed. "
    "Extract exactly these fields from the record text below:\n"
    "- khasra_no: the khewat/khasra/gata number (खसरा/खेवट/गाता नंबर)\n"
    "- owner_name: the cultivator/owner name (किसान/स्वामी/खातेदार का नाम)\n"
    "- area_raw: the land area exactly as printed (रकबा/क्षेत्रफल, e.g. '२-४० bigha')\n"
    "- village: the village name (गाँव/मौजा)\n"
    "Keep each value in its printed script (Devanagari stays Devanagari). "
    "If a field is unreadable or absent, return null for it instead of guessing."
)
