import hashlib
import hmac
import secrets

from app.config import settings

_runtime_secret = secrets.token_bytes(32)


def _secret() -> bytes:
    return settings.APP_SECRET.encode() if settings.APP_SECRET else _runtime_secret


def make_call_token(call_id: int) -> str:
    message = f"call:{call_id}".encode()
    return hmac.new(_secret(), message, hashlib.sha256).hexdigest()


def valid_call_token(call_id: int, token: str | None) -> bool:
    if not token:
        return False
    return hmac.compare_digest(
        make_call_token(call_id).encode(),
        token.encode(),
    )


def valid_password(password: str | None) -> bool:
    if not settings.HISTORY_PASSWORD or not password:
        return False
    return hmac.compare_digest(
        settings.HISTORY_PASSWORD.encode(),
        password.encode(),
    )


def require_history_access(
    call_id: int | None,
    password: str | None,
    token: str | None,
    *,
    list_access: bool = False,
    require_credentials: bool = False,
) -> None:
    if not settings.HISTORY_PASSWORD and not require_credentials:
        return
    if valid_password(password) or (
        not list_access and call_id is not None and valid_call_token(call_id, token)
    ):
        return
    from fastapi import HTTPException

    raise HTTPException(status_code=401, detail="Valid history password or call token required.")
