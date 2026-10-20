import asyncio
import logging
from pathlib import Path
from uuid import uuid4

import httpx

from app.config import settings

logger = logging.getLogger(__name__)
LOCAL_RECORDINGS = Path("recordings")


def configured() -> bool:
    return bool(settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY)


async def save_recording(call_id: int, content: bytes, content_type: str) -> str:
    extension = "webm" if "webm" in content_type else "ogg"
    object_key = f"call-{call_id}/{uuid4().hex}.{extension}"
    if configured():
        url = (
            f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/"
            f"{settings.SUPABASE_BUCKET_NAME}/{object_key}"
        )
        headers = {
            "Authorization": "Bearer " + settings.SUPABASE_SERVICE_ROLE_KEY,
            "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
            "Content-Type": content_type,
            "x-upsert": "false",
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = None
            for attempt in range(2):
                try:
                    response = await client.post(url, content=content, headers=headers)
                    break
                except httpx.TimeoutException:
                    if attempt == 1:
                        raise
                    await asyncio.sleep(1)
        if response.is_error:
            logger.error("Supabase recording upload failed with status %s.", response.status_code)
            raise RuntimeError("Recording storage is unavailable.")
        return object_key

    LOCAL_RECORDINGS.mkdir(exist_ok=True)
    path = LOCAL_RECORDINGS / object_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return object_key


async def signed_recording_url(object_key: str) -> str:
    if not configured():
        return f"/api/calls/local/{object_key}"
    url = (
        f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/sign/"
        f"{settings.SUPABASE_BUCKET_NAME}/{object_key}"
    )
    headers = {
        "Authorization": "Bearer " + settings.SUPABASE_SERVICE_ROLE_KEY,
        "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json={"expiresIn": 300}, headers=headers)
    if response.is_error:
        logger.error("Supabase recording URL failed with status %s.", response.status_code)
        raise RuntimeError("Recording playback is unavailable.")
    signed_path = response.json().get("signedURL")
    if not signed_path:
        raise RuntimeError("Recording playback is unavailable.")
    return (
        signed_path
        if signed_path.startswith("http")
        else f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1{signed_path}"
    )
