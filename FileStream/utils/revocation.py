"""In-process registry of revoked (deleted) files.

The bot and the web server share the same process, so deleting a file can mark
its database id here and the stream routes reject it immediately without a
database round trip. Signed stream URLs also carry an expiry, which bounds how
long an already-saved URL keeps working after a restart or on another instance.
"""

from __future__ import annotations

_revoked: set = set()


def mark_revoked(db_id) -> None:
    """Remember that ``db_id`` has been deleted."""
    if db_id is not None:
        _revoked.add(str(db_id))


def is_revoked(db_id) -> bool:
    """Return ``True`` if ``db_id`` was revoked in this process."""
    return db_id is not None and str(db_id) in _revoked
