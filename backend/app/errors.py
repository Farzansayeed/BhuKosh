from fastapi import Request
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
    body: dict = {"type": exc.type, "title": exc.title, "status": exc.status}
    if exc.detail:
        body["detail"] = exc.detail
    body.update(exc.extra)
    return JSONResponse(status_code=exc.status, media_type="application/problem+json", content=body)
