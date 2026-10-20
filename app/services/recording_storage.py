import logging
import asyncio
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings

logger = logging.getLogger(__name__)
LOCAL_RECORDINGS = Path("recordings")


def configured() -> bool:
    return bool(
        settings.R2_ACCOUNT_ID
        and settings.R2_ACCESS_KEY_ID
        and settings.R2_SECRET_ACCESS_KEY
        and settings.R2_BUCKET_NAME
        and settings.R2_ENDPOINT_URL
    )


def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT_URL,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


async def save_recording(call_id: int, content: bytes, content_type: str) -> str:
    extension = "webm" if "webm" in content_type else "ogg"
    object_key = f"call-{call_id}/{uuid4().hex}.{extension}"
    if configured():
        try:
            await asyncio.to_thread(
                _client().put_object,
                Bucket=settings.R2_BUCKET_NAME,
                Key=object_key,
                Body=content,
                ContentType=content_type,
            )
        except (BotoCoreError, ClientError):
            logger.exception("R2 recording upload failed.")
            raise RuntimeError("Recording storage is unavailable.")
        return object_key

    if not settings.ALLOW_LOCAL_RECORDINGS:
        raise RuntimeError("Recording storage is unavailable.")
    LOCAL_RECORDINGS.mkdir(exist_ok=True)
    path = LOCAL_RECORDINGS / object_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return object_key


async def signed_recording_url(object_key: str) -> str:
    if not configured():
        if not settings.ALLOW_LOCAL_RECORDINGS:
            raise RuntimeError("Recording playback is unavailable.")
        return f"/api/calls/local/{object_key}"
    try:
        return await asyncio.to_thread(
            _client().generate_presigned_url,
            "get_object",
            Params={"Bucket": settings.R2_BUCKET_NAME, "Key": object_key},
            ExpiresIn=300,
        )
    except (BotoCoreError, ClientError):
        logger.exception("R2 recording URL generation failed.")
        raise RuntimeError("Recording playback is unavailable.")
