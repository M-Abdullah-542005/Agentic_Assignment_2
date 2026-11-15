"""Authentication: passwords (scrypt), sessions stored in the database, FastAPI dependency.

* Passwords are hashed with scrypt (standard library) and a random salt; they are never stored or logged in clear.
* A session token is 32 random bytes. Only its SHA-256 is stored, with an expiry, so a stolen database cannot
  be used to log in and logging out / expiry really end the session. Sessions survive an API restart.
* The user's marketplace token is stored encrypted (Fernet, key from GIGBID_SECRET_KEY) in the database.
"""
import hashlib
import hmac
import os

from fastapi import Header, HTTPException

from db import get_db

_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=_N, r=_R, p=_P, dklen=32)
        return hmac.compare_digest(got, bytes.fromhex(digest))
    except Exception:
        return False


def bearer_token(authorization: str) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def current_user(authorization: str = Header(default="")) -> str:
    token = bearer_token(authorization)
    uid = get_db().user_for_session(token) if token else None
    if uid is None:
        raise HTTPException(401, "invalid or expired session")
    return uid
