"""YouTube OAuth — installed-app OAuth with Fernet-encrypted token storage."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
_TOKEN_FILE = Path.home() / ".shortsforge" / "credentials.json"
_KEYRING_SERVICE = "shortsforge"
_KEYRING_KEY = "youtube-fernet"


def _check_file_permissions(path: Path) -> None:
    """Reject if file is world-readable."""
    try:
        mode = path.stat().st_mode
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            raise PermissionError(
                f"Client secret file {path} is group/world readable. "
                "Run: chmod 600 <file>"
            )
    except OSError:
        pass  # File may not exist yet


def _get_fernet():
    """Get or create a Fernet key from the OS keyring."""

    import keyring
    from cryptography.fernet import Fernet

    key = keyring.get_password(_KEYRING_SERVICE, _KEYRING_KEY)
    if not key:
        key = Fernet.generate_key().decode()
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_KEY, key)
    return Fernet(key.encode() if isinstance(key, str) else key)


def _encrypt_token(data: str) -> bytes:
    return _get_fernet().encrypt(data.encode())


def _decrypt_token(data: bytes) -> str:
    return _get_fernet().decrypt(data).decode()


def run_oauth_flow() -> None:
    """Run the installed-app OAuth flow and store encrypted credentials."""
    from google_auth_oauthlib.flow import (
        InstalledAppFlow,  # type: ignore[import-untyped]
    )

    client_secret_path = os.environ.get("YOUTUBE_CLIENT_SECRET_PATH")
    if not client_secret_path:
        raise RuntimeError("YOUTUBE_CLIENT_SECRET_PATH env var not set")

    secret_file = Path(client_secret_path)
    if not secret_file.exists():
        raise FileNotFoundError(f"Client secret file not found: {secret_file}")

    _check_file_permissions(secret_file)

    flow = InstalledAppFlow.from_client_secrets_file(str(secret_file), scopes=_SCOPES)
    creds = flow.run_local_server(port=0)

    # Encrypt and store
    token_data = creds.to_json()
    encrypted = _encrypt_token(token_data)
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_FILE.write_bytes(encrypted)
    try:
        os.chmod(_TOKEN_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    logger.info("youtube_auth.token_stored")


def get_youtube_service():
    """Return an authenticated YouTube service, refreshing token if needed."""
    import json

    from google.oauth2.credentials import Credentials  # type: ignore[import-untyped]
    from googleapiclient.discovery import build  # type: ignore[import-untyped]

    if not _TOKEN_FILE.exists():
        raise RuntimeError(
            "No YouTube credentials found. Run `shortsforge auth youtube` first."
        )

    encrypted = _TOKEN_FILE.read_bytes()
    token_json = _decrypt_token(encrypted)
    creds = Credentials.from_authorized_user_info(json.loads(token_json), _SCOPES)

    if not creds.valid:
        from google.auth.transport.requests import (
            Request,  # type: ignore[import-untyped]
        )

        creds.refresh(Request())
        # Re-encrypt and save
        _TOKEN_FILE.write_bytes(_encrypt_token(creds.to_json()))

    return build("youtube", "v3", credentials=creds)
