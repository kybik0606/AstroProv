"""Shared utility functions for AstroProv."""

import hashlib
from pathlib import Path

CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path | str) -> str:
    """Calculate a file's SHA-256 hash using bounded memory."""
    path = Path(path)
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(CHUNK_SIZE), b""):
            digest.update(chunk)

    return digest.hexdigest()