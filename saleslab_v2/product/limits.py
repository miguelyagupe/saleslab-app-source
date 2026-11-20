"""Bound multipart requests before FastAPI spools their file bodies."""

from fastapi import HTTPException
from fastapi.responses import JSONResponse


class FinancialUploadLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") not in {
            "/financial-v2/preview",
            "/financial-v2/generate",
            "/financial-v2/inspect",
            "/financial-v2/month-end/preview",
            "/financial-v2/month-end/snapshots",
        }:
            return await self.app(scope, receive, send)
        limit = 6 * 1024 * 1024
        headers = dict(scope.get("headers", ()))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = limit + 1
        if length > limit or length < 0:
            return await JSONResponse(
                {"detail": "Upload exceeds the request limit."}, status_code=413
            )(scope, receive, send)
        size = 0

        async def bounded_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > limit:
                raise HTTPException(413, "Upload exceeds the request limit.")
            return message

        return await self.app(scope, bounded_receive, send)
