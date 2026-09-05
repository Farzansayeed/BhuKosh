from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class Problem(Exception):
    """RFC 7807 problem detail. Raise from anywhere; rendered as application/problem+json."""

    def __init__(self, status: int, title: str, detail: str | None = None, type_: str = "about:blank", **extra):
        self.status = status
        self.title = title
        self.detail = detail
        self.type = type_
        self.extra = extra
        super().__init__(f"{status} {title}: {detail}")


async def problem_handler(request: Request, exc: Problem) -> JSONResponse:
    extra = dict(exc.extra)
    retry_after = extra.pop("retry_after_seconds", None)  # goes to the header, not the body
    body: dict = {"type": exc.type, "title": exc.title, "status": exc.status}
    if exc.detail:
        body["detail"] = exc.detail
    body.update(extra)
    resp = JSONResponse(status_code=exc.status, media_type="application/problem+json", content=body)
    if retry_after is not None:
        resp.headers["Retry-After"] = str(retry_after)
    return resp


async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Keep the RFC-7807 contract for request validation (422) too."""
    return JSONResponse(
        status_code=422,
        media_type="application/problem+json",
        content={
            "type": "https://httpstatuses.com/422",
            "title": "Validation Failed",
            "status": 422,
            "errors": jsonable_encoder(exc.errors()),
        },
    )
