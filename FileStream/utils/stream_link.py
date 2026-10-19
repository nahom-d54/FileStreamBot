"""Self-contained streaming URLs.

A Telegram file id (together with a few media attributes) is everything needed
to stream a file straight from Telegram. Those file ids are too long to be used
inside Telegram inline buttons, so buttons keep pointing at the short
``/dl/<db_id>`` URL which redirects to ``/stream/<token>``. Media players follow
that redirect once and then keep requesting the long URL directly, which means
range requests no longer touch the database at all.

The token is a compact JSON payload (file ids per client, size, mime type and
file name) encoded as URL-safe base64 and signed with HMAC-SHA256 so it cannot
be forged. The signing key comes from ``configure_secret`` (``STREAM_SECRET`` or
a value generated once and stored in the database), so it is stable across
restarts and does not depend on ``API_HASH``. Tokens also embed an issue time
and an expiry (``STREAM_TOKEN_TTL``, 24h by default) so a copied URL stops
working on its own.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Any, Dict, Optional

from pyrogram.file_id import FileId

from FileStream.bot import FileStream
from FileStream.config import Server
from FileStream.server.exceptions import InvalidHash, LinkExpired

TOKEN_VERSION = "s1"
_SIGNATURE_LENGTH = 16

_secret: Optional[bytes] = None


def configure_secret(secret: str | bytes) -> None:
    """Set the key used to sign stream tokens."""
    global _secret
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    if not secret:
        raise ValueError("Stream token secret cannot be empty")
    _secret = secret


def _signing_key() -> bytes:
    if _secret is None:
        raise RuntimeError(
            "Stream token secret is not configured; call configure_secret() first"
        )
    return _secret


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(encoded_payload: str) -> str:
    digest = hmac.new(
        _signing_key(), encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()
    return _b64encode(digest)[:_SIGNATURE_LENGTH]


def build_stream_token(file_info: dict) -> str:
    """Build a signed token that fully describes how to stream a file."""
    file_ids = {
        str(client_id): file_id
        for client_id, file_id in (file_info.get("file_ids") or {}).items()
        if file_id
    }
    primary_file_id = file_info.get("file_id")
    if primary_file_id:
        file_ids.setdefault(str(getattr(FileStream, "id", "")), primary_file_id)

    issued_at = int(time.time())
    payload = {
        "d": str(file_info["_id"]),
        "s": int(file_info.get("file_size") or 0),
        "m": file_info.get("mime_type") or "application/octet-stream",
        "n": file_info.get("file_name") or "",
        "f": file_ids,
        "t": issued_at,
    }
    ttl = int(getattr(Server, "STREAM_TOKEN_TTL", 0) or 0)
    if ttl > 0:
        payload["e"] = issued_at + ttl
    encoded = _b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    return f"{TOKEN_VERSION}.{encoded}.{_sign(encoded)}"


def stream_url(file_info: dict) -> str:
    """Absolute self-contained streaming URL for a file database record."""
    return urllib.parse.urljoin(
        Server.URL, f"stream/{build_stream_token(file_info)}"
    )


class StreamRef:
    """Media reference decoded from a stream token."""

    __slots__ = (
        "db_id",
        "file_size",
        "mime_type",
        "file_name",
        "issued_at",
        "expires_at",
        "_file_ids",
    )

    def __init__(self, payload: Dict[str, Any]):
        self.db_id = payload.get("d") or None
        self.file_size = int(payload.get("s") or 0)
        self.mime_type = payload.get("m") or "application/octet-stream"
        self.file_name = payload.get("n") or ""
        self.issued_at = payload.get("t")
        self.expires_at = payload.get("e")
        self._file_ids = payload.get("f") or {}

    def file_id_for(self, client) -> Optional[FileId]:
        """Return the Telegram file id that belongs to ``client``, if known."""
        raw_file_id = self._file_ids.get(str(getattr(client, "id", "")))
        if not raw_file_id:
            return None
        try:
            file_id = FileId.decode(raw_file_id)
        except Exception:
            return None
        file_id.file_size = self.file_size
        file_id.mime_type = self.mime_type
        file_id.file_name = self.file_name
        return file_id


def parse_stream_token(token: str) -> StreamRef:
    """Validate and decode a stream token.

    Raises ``InvalidHash`` for malformed/forged tokens and ``LinkExpired`` when
    the embedded expiry has passed.
    """
    try:
        version, encoded, signature = token.split(".")
    except ValueError:
        raise InvalidHash

    if version != TOKEN_VERSION or not hmac.compare_digest(
        signature, _sign(encoded)
    ):
        raise InvalidHash

    try:
        payload = json.loads(_b64decode(encoded))
    except (ValueError, TypeError):
        raise InvalidHash

    if not isinstance(payload, dict) or not payload.get("f"):
        raise InvalidHash

    expires_at = payload.get("e")
    if expires_at is not None and int(expires_at) <= int(time.time()):
        raise LinkExpired

    return StreamRef(payload)
