"""YouTube publishing with OAuth, rate limiting, and consent enforcement."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel

from shortsforge.security.moderation import check_text
from shortsforge.security.rate_limit import (
    UPLOAD_DAY_BUCKET,
    UPLOAD_HOUR_BUCKET,
    RateLimitExceeded,
)

logger = structlog.get_logger(__name__)

_AUDIT_LOG = Path.home() / ".shortsforge" / "audit.log"
_MAX_TITLE_LEN = 100
_MAX_DESC_LEN = 5000


class UploadResult(BaseModel):
    video_id: str
    url: str
    visibility: str
    title: str


def _write_audit(
    *,
    clip_sha256: str,
    title: str,
    visibility: str,
    consent_token_present: bool,
    turn_id: str = "unknown",
    channel_id: str = "unknown",
    error: str | None = None,
) -> None:
    _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "clip_sha256": clip_sha256,
        "title": title[:50],  # truncate for audit log
        "channel_id": channel_id,
        "visibility": visibility,
        "consent_token_present": consent_token_present,
        "turn_id": turn_id,
        "error": error,
    }
    with open(_AUDIT_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    try:
        os.chmod(_AUDIT_LOG, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _sanitize_text(text: str, max_len: int) -> str:
    """Strip control chars and length-limit."""
    import unicodedata

    cleaned = "".join(
        c for c in text if unicodedata.category(c)[0] != "C" or c in "\n\r\t"
    )
    return cleaned[:max_len]


async def publish_youtube(
    *,
    clip_id: str,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    visibility: Literal["private", "unlisted", "public"] = "unlisted",
    consent_token: str | None = None,
) -> UploadResult:
    """Publish a clip to YouTube with safety guardrails.

    - public visibility requires a valid consent_token
    - Rate-limited to 6/hour, 20/day
    - Text moderation on title + description
    - Append-only audit log for every attempt
    """
    clip_sha = hashlib.sha256(clip_id.encode()).hexdigest()
    tags = tags or []

    # Sanitize inputs
    title_clean = _sanitize_text(title, _MAX_TITLE_LEN)
    desc_clean = _sanitize_text(description, _MAX_DESC_LEN)
    if "#Shorts" not in desc_clean:
        desc_clean = (desc_clean + " #Shorts").strip()

    # Consent check for public uploads
    if visibility == "public":
        expected_token = hashlib.sha256(f"{clip_id}{title_clean}".encode()).hexdigest()
        if not consent_token or consent_token != expected_token:
            _write_audit(
                clip_sha256=clip_sha,
                title=title_clean,
                visibility=visibility,
                consent_token_present=consent_token is not None,
                error="missing_or_invalid_consent_token",
            )
            raise PermissionError(
                "Public upload requires a valid consent_token. "
                "Call request_publish_consent first."
            )

    # Rate limit check
    try:
        UPLOAD_HOUR_BUCKET.consume(1)
        UPLOAD_DAY_BUCKET.consume(1)
    except RateLimitExceeded as exc:
        _write_audit(
            clip_sha256=clip_sha,
            title=title_clean,
            visibility=visibility,
            consent_token_present=consent_token is not None,
            error=f"rate_limit: {exc}",
        )
        raise

    # Text moderation
    if not await check_text(title_clean + " " + desc_clean):
        _write_audit(
            clip_sha256=clip_sha,
            title=title_clean,
            visibility=visibility,
            consent_token_present=consent_token is not None,
            error="moderation_rejected",
        )
        raise ValueError("Content rejected by moderation")

    # Actual upload via YouTube Data API
    try:
        from shortsforge.publishing.youtube_auth import get_youtube_service

        youtube = get_youtube_service()
        clip_path = _get_clip_path(clip_id)

        from googleapiclient.http import MediaFileUpload  # type: ignore[import-untyped]

        request_body = {
            "snippet": {
                "title": title_clean,
                "description": desc_clean,
                "tags": tags[:30],
                "categoryId": "22",  # People & Blogs
            },
            "status": {
                "privacyStatus": visibility,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(str(clip_path), chunksize=-1, resumable=True)
        upload_request = youtube.videos().insert(
            part="snippet,status",
            body=request_body,
            media_body=media,
        )
        response = upload_request.execute()
        video_id = response["id"]

        _write_audit(
            clip_sha256=clip_sha,
            title=title_clean,
            visibility=visibility,
            consent_token_present=consent_token is not None,
        )

        logger.info("publish_youtube.success", video_id=video_id, visibility=visibility)
        return UploadResult(
            video_id=video_id,
            url=f"https://youtube.com/shorts/{video_id}",
            visibility=visibility,
            title=title_clean,
        )
    except Exception as exc:
        _write_audit(
            clip_sha256=clip_sha,
            title=title_clean,
            visibility=visibility,
            consent_token_present=consent_token is not None,
            error=str(exc)[:200],
        )
        raise


def _get_clip_path(clip_id: str) -> Path:
    """Look up a clip path from the workspace registry."""
    workspace_file = Path.home() / ".shortsforge" / "workspace.json"
    if workspace_file.exists():
        data = json.loads(workspace_file.read_text())
        if clip_id in data:
            return Path(data[clip_id]["path"])
    raise FileNotFoundError(f"Clip {clip_id!r} not found in workspace")
