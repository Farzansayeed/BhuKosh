from fastapi import APIRouter, Depends

from .auth.dependencies import get_current_user
from .errors import Problem
from .verification import verify_record

router = APIRouter(tags=["verification"])


@router.get("/records/{record_id}/verify")
def verify(record_id: int, user: dict = Depends(get_current_user)):
    """Truth assurance for one record: a composite verdict over every checkable
    signal (business rules, cross-record corroboration, document integrity,
    human authority), with the external-registry cross-check reported honestly
    as an adapter slot. This is deliberately separate from extraction
    confidence (how well the pixels were read)."""
    result = verify_record(record_id)
    if result is None:
        raise Problem(404, "Not Found", "No such record.")
    return result
