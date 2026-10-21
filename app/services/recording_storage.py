import asyncio
import logging
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings

logger = logging.getLogger(__name__)
LOCAL_RECORDINGS = Path("recordings")


class RecordingStorageError(RuntimeError):
    def __init__(self, message: str, *, missing_object: bool = False):
        super().__init__(message)
        self.missing_object = missing_object


def configured() -> bool:
    return bool(
        settings.STORAGE_ENDPOINT_URL
        and settings.STORAGE_REGION
        and settings.STORAGE_ACCESS_KEY_ID
        and settings.STORAGE_SECRET_ACCESS_KEY
        and settings.STORAGE_BUCKET_NAME
    )


def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.STORAGE_ENDPOINT_URL,
        aws_access_key_id=settings.STORAGE_ACCESS_KEY_ID,
        aws_secret_access_key=settings.STORAGE_SECRET_ACCESS_KEY,
        region_name=settings.STORAGE_REGION,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )


async def save_recording(call_id: int, content: bytes, content_type: str) -> str:
    extension = "webm" if "webm" in content_type else "ogg"
    object_key = f"call-{call_id}/{uuid4().hex}.{extension}"
    if configured():
        try:
            await asyncio.to_thread(
                _client().put_object,
                Bucket=settings.STORAGE_BUCKET_NAME,
                Key=object_key,
                Body=content,
                ContentType=content_type,
            )
        except (BotoCoreError, ClientError):
            logger.exception("Storage recording upload failed.")
            raise RecordingStorageError("Recording storage is unavailable.")
        return object_key

    if not settings.ALLOW_LOCAL_RECORDINGS:
        raise RuntimeError("Recording storage is unavailable.")
    try:
        LOCAL_RECORDINGS.mkdir(exist_ok=True)
        path = LOCAL_RECORDINGS / object_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    except OSError:
        logger.exception("Local recording upload failed.")
        raise RecordingStorageError("Recording storage is unavailable.")
    return object_key


async def signed_recording_url(object_key: str) -> str:
    if not configured():
        if not settings.ALLOW_LOCAL_RECORDINGS:
            raise RuntimeError("Recording playback is unavailable.")
        path = (LOCAL_RECORDINGS / object_key).resolve()
        root = LOCAL_RECORDINGS.resolve()
        if root not in path.parents or not path.is_file():
            raise RecordingStorageError(
                "Recording playback object is missing.", missing_object=True
            )
        return f"/api/calls/local/{object_key}"
    try:
        await asyncio.to_thread(
            _client().head_object,
            Bucket=settings.STORAGE_BUCKET_NAME,
            Key=object_key,
        )
        return await asyncio.to_thread(
            _client().generate_presigned_url,
            "get_object",
            Params={"Bucket": settings.STORAGE_BUCKET_NAME, "Key": object_key},
            ExpiresIn=300,
        )
    except ClientError as exc:
        error_code = str(exc.response.get("Error", {}).get("Code", ""))
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            raise RecordingStorageError(
                "Recording playback object is missing.", missing_object=True
            ) from exc
        logger.exception("Storage recording URL generation failed.")
        raise RecordingStorageError("Recording playback is unavailable.") from exc
    except BotoCoreError as exc:
        logger.exception("Storage recording URL generation failed.")
        raise RecordingStorageError("Recording playback is unavailable.") from exc


async def delete_recording(object_key: str) -> None:
    if configured():
        try:
            await asyncio.to_thread(
                _client().delete_object,
                Bucket=settings.STORAGE_BUCKET_NAME,
                Key=object_key,
            )
        except (BotoCoreError, ClientError):
            logger.exception("Storage recording cleanup failed.")
        return
    path = (LOCAL_RECORDINGS / object_key).resolve()
    root = LOCAL_RECORDINGS.resolve()
    if root in path.parents and path.is_file():
        try:
            path.unlink()
        except OSError:
            logger.exception("Local recording cleanup failed.")
