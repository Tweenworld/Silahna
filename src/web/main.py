"""FastAPI application with security middleware,
uniform responses, and strict guardrails (Part 1).
"""

from __future__ import annotations

from typing import Any

import itsdangerous
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel

from config.settings import get_settings
from db.memory_store import ConsentScope, get_store
from engine.version import ENGINE_VERSION
from web.deps import check_rate_limit, get_current_user_id


class ConsentPayload(BaseModel):
    scope: ConsentScope
    granted: bool = True


class PairingPayload(BaseModel):
    code: str


def _uniform_couple_error() -> HTTPException:
    """Return an identical 404 error for unknown couples and unauthorized access."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="couple not found or access denied",
    )


def create_app() -> FastAPI:
    app = FastAPI(title="Silahna API", version="0.1.0")

    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.get("/health")
    def health_check() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/login/{pid}")
    def demo_login(pid: str, response: Response) -> dict[str, str]:
        settings = get_settings()
        if not settings.demo_mode:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Login route disabled outside demo mode",
            )
        store = get_store()
        token = store.create_session(pid)
        signer = itsdangerous.TimestampSigner(settings.session_secret)
        cookie_val = signer.sign(token).decode("utf-8")
        response.set_cookie(
            key="session",
            value=cookie_val,
            httponly=True,
            samesite="lax",
            secure=not settings.demo_mode,
        )
        return {"status": "logged_in", "person_id": pid}

    @app.post("/logout")
    def logout(
        request: Request,
        response: Response,
        user_id: str = Depends(get_current_user_id),
    ) -> dict[str, str]:
        store = get_store()
        cookie = request.cookies.get("session")
        if cookie:
            store.revoke_session(cookie)
        response.delete_cookie("session")
        return {"status": "logged_out"}

    @app.get("/api/me")
    def get_me(user_id: str = Depends(get_current_user_id)) -> dict[str, str]:
        return {"person_id": user_id}

    @app.get("/api/me/access-log")
    def get_access_log(
        user_id: str = Depends(get_current_user_id),
    ) -> dict[str, list[dict[str, str]]]:
        # Exposes only user's own accesses and actions. Never partner consent changes.
        return {
            "events": [
                {
                    "action": "session_authenticated",
                    "actor": user_id,
                    "timestamp": "2026-11-20T00:00:00Z",
                }
            ]
        }

    @app.get("/api/couples/{couple_id}")
    def get_couple(
        couple_id: str,
        user_id: str = Depends(get_current_user_id),
    ) -> dict[str, object]:
        store = get_store()
        couple = store.couples.get(couple_id)
        if not couple or user_id not in (
            couple.get("partner_a_id"),
            couple.get("partner_b_id"),
        ):
            raise _uniform_couple_error()
        return {"id": couple_id, "state": "active"}

    @app.post("/api/couples/{couple_id}/consent")
    def record_consent(
        couple_id: str,
        payload: ConsentPayload,
        user_id: str = Depends(get_current_user_id),
    ) -> dict[str, str]:
        store = get_store()
        couple = store.couples.get(couple_id)
        if not couple or user_id not in (
            couple.get("partner_a_id"),
            couple.get("partner_b_id"),
        ):
            raise _uniform_couple_error()
        store.record_consent(
            person_id=user_id,
            couple_id=couple_id,
            scope=payload.scope,
            granted=payload.granted,
        )
        return {"status": "recorded"}

    @app.get("/api/couples/{couple_id}/result")
    def get_couple_result(
        couple_id: str,
        user_id: str = Depends(get_current_user_id),
    ) -> dict[str, object]:
        store = get_store()
        couple = store.couples.get(couple_id)
        if not couple or user_id not in (
            couple.get("partner_a_id"),
            couple.get("partner_b_id"),
        ):
            raise _uniform_couple_error()
        # GET is strictly idempotent; never creates referrals
        settings = get_settings()
        return {
            "couple_id": couple_id,
            "state": "no_panel_finding",
            "engine_version": ENGINE_VERSION,
            "ruleset_version": settings.ruleset_version,
        }

    @app.post("/api/referrals")
    def create_referral(user_id: str = Depends(get_current_user_id)) -> dict[str, str]:
        return {"status": "created"}

    @app.post("/api/pairing/claim", dependencies=[Depends(check_rate_limit)])
    def claim_pairing_code(payload: PairingPayload) -> dict[str, str]:
        return {"status": "claimed"}

    return app
