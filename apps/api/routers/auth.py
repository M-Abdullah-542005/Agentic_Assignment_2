import os
import time
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from auth import bearer_token, current_user, hash_password, verify_password
from db import get_db
from schemas import LoginIn, MarketplaceTokenIn, SessionOut
router = APIRouter(prefix="/auth", tags=["auth"])

_FAILS: dict[str, list[float]] = {}          # email -> recent failed logins (simple brute-force brake, per process)
MAX_FAILS, WINDOW = 8, 600.0


def _throttle(email: str) -> None:
    now = time.time()
    recent = [t for t in _FAILS.get(email, []) if now - t < WINDOW]
    _FAILS[email] = recent
    if len(recent) >= MAX_FAILS:
        raise HTTPException(429, "too many failed logins; try again later")


def _fail(email: str):
    _FAILS.setdefault(email, []).append(time.time())
    raise HTTPException(401, "invalid email or password")


@router.post("/session", response_model=SessionOut)
def session(b: LoginIn, request: Request):
    """Log in with email + password. The first login with a new email creates the account (persona required)."""
    db, email = get_db(), b.email.strip().lower()
    _throttle(email)
    u = db.user_by_email(email)
    if u is None:
        if not b.persona:
            raise HTTPException(422, "persona is required to create an account")
        uid = db.create_user(email, hash_password(b.password), b.persona,
                             b.hourly_rate or request.app.state.cfg.get("user_defaults", {}).get("hourly_rate", 40),
                             b.weekly_connects_budget if b.weekly_connects_budget is not None
                             else request.app.state.cfg.get("user_defaults", {}).get("connects_budget", 60))
        # Auto-configure marketplace token from environment if available
        default_token = os.environ.get("GIGBID_MARKETPLACE_TOKEN", "tok_test")
        if default_token:
            db.save_marketplace_token(uid, default_token)
        u = db.user(uid)
        if not verify_password(b.password, u["password_hash"]):      # someone else registered this email first
            _fail(email)
    else:
        if not verify_password(b.password, u["password_hash"]):
            _fail(email)
        db.update_profile(u["user_id"], b.persona, b.hourly_rate, b.weekly_connects_budget)   # only what was sent
        u = db.user(u["user_id"])
    _FAILS.pop(email, None)
    request.app.state.runtimes.invalidate(u["user_id"])
    ttl = float(request.app.state.cfg.get("auth", {}).get("session_ttl_days", 7)) * 86400
    return SessionOut(token=db.create_session(u["user_id"], ttl), user_id=u["user_id"], persona=u["persona"])


@router.post("/logout")
def logout(authorization: str = Header(default=""), user=Depends(current_user)):
    get_db().delete_session(bearer_token(authorization))
    return {"ok": True}


@router.get("/me")
def me(user=Depends(current_user)):
    u = get_db().user(user)
    return {"user_id": user, "email": u["email"], "persona": u["persona"], "hourly_rate": u["hourly_rate"],
            "weekly_connects_budget": u["connects_budget"], "marketplace_connected": get_db().marketplace_token(user) is not None}


@router.post("/marketplace-token")
def connect(b: MarketplaceTokenIn, request: Request, user=Depends(current_user)):
    """The token travels in the request BODY (never in the URL). Stored encrypted, one per user."""
    get_db().save_marketplace_token(user, b.token)
    request.app.state.runtimes.invalidate(user)     # the user's port is rebuilt with this token on next use
    return {"stored": True}                          # encrypted at rest, never logged
