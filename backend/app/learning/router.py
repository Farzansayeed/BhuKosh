from fastapi import APIRouter, Depends

from ..auth.dependencies import get_current_user
from . import service

router = APIRouter(prefix="/learning", tags=["learning"])


@router.get("/hints")
def hints(user: dict = Depends(get_current_user)):
    """The live learning loop, inspectable: the exact few-shot block currently
    injected into every extraction prompt, with the correction trail behind it."""
    return service.hints_for_display()
