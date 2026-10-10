"""Strict management-only source intent; GET never initializes metadata."""
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..contracts import ContractModel, TelegramId
from .auth import SESSION_COOKIE


class _SourceSelection(ContractModel):
    source_id: TelegramId


def install_first_value_routes(app, context, require_session, require_write_session):
    def service(session):
        if session.profile_id != context.settings_getter().profile_id:
            raise HTTPException(status_code=403, detail="owner_pairing_required")
        try:
            owner = context.first_value_getter() if context.first_value_getter is not None else None
        except Exception:
            owner = None
        if owner is None:
            raise HTTPException(status_code=503, detail="first_value_unavailable")
        return owner

    def projection(owner):
        try:
            return JSONResponse(owner.selection_status().model_dump(mode="json"))
        except PermissionError:
            raise HTTPException(status_code=403, detail="owner_pairing_required") from None
        except ValueError:
            raise HTTPException(status_code=503, detail="first_value_unavailable") from None

    @app.get("/api/v1/onboarding/first-source")
    async def first_source(session=Depends(require_session)):
        return projection(service(session))

    @app.post("/api/v1/onboarding/source-selection")
    async def source_selection(request: Request, session=Depends(require_write_session)):
        if not request.headers.get("origin"):
            raise HTTPException(status_code=403, detail="origin_denied")
        body = bytearray()
        try:
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 1024:
                    raise ValueError()
            payload = _SourceSelection.model_validate_json(body)
        except (ValueError, TypeError, RecursionError):
            raise HTTPException(status_code=422, detail="source_selection_invalid") from None
        # Body consumption can outlive either the session or current owner admission.
        current = await require_session(request.cookies.get(SESSION_COOKIE))
        await require_write_session(request, current, request.headers.get("x-csrf-token"))
        owner = service(current)
        try:
            await owner.select_source(payload.source_id)
        except PermissionError:
            raise HTTPException(status_code=403, detail="owner_pairing_required") from None
        except ValueError:
            raise HTTPException(status_code=503, detail="first_value_unavailable") from None
        return projection(owner)

    @app.post("/api/v1/onboarding/first-source/preview", status_code=201)
    async def first_source_preview(request: Request, session=Depends(require_write_session)):
        if not request.headers.get("origin"):
            raise HTTPException(status_code=403, detail="origin_denied")
        body = bytearray()
        try:
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 1024:
                    raise ValueError()
            payload = _SourceSelection.model_validate_json(body)
        except (ValueError, TypeError, RecursionError):
            raise HTTPException(status_code=422, detail="source_selection_invalid") from None
        current = await require_session(request.cookies.get(SESSION_COOKIE))
        await require_write_session(request, current, request.headers.get("x-csrf-token"))
        owner = service(current)
        try:
            async with context.database.session() as db:
                action = await owner.preview.create(db, payload.source_id, 1000)
                current = await require_session(request.cookies.get(SESSION_COOKIE))
                await require_write_session(request, current, request.headers.get("x-csrf-token"))
                if service(current) is not owner:
                    raise PermissionError("owner_pairing_required")
                owner._admit()
        except PermissionError:
            raise HTTPException(status_code=403, detail="first_source_preview_denied") from None
        except ValueError:
            raise HTTPException(status_code=409, detail="first_source_preview_unavailable") from None
        from .app import _action_json
        return _action_json(action)
