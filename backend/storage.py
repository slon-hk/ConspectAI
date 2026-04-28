"""
Content-addressed file storage with deduplication + compression.

Strategy:
  - Each file stored by SHA-256(content) → zero duplication across all users.
  - Text/code/JSON/CSV → gzip compressed (.gz suffix).
  - Images/PDF → stored as-is (already compressed formats).
  - Directory layout: uploads/<first2>/<next2>/<rest>  (avoids FS inode limits)
  - A PostgreSQL `files` table tracks: hash, mime, original_size, stored_size, ref_count.
  - ref_count > 0 keeps the file; on delete we decrement and GC if 0.
"""

import gzip
import hashlib
import mimetypes
from pathlib import Path

UPLOADS_DIR = Path("uploads")
UPLOADS_DIR.mkdir(exist_ok=True)

# MIME types that compress well with gzip
COMPRESSIBLE = {
    "text/plain", "text/html", "text/css", "text/csv",
    "application/json", "application/xml",
    "text/x-python", "application/javascript", "text/markdown",
    "text/x-tex", "application/x-tex",
}


def _should_compress(mime: str) -> bool:
    return mime in COMPRESSIBLE or mime.startswith("text/")


def _hash_path(sha256: str, compressed: bool) -> Path:
    suffix = ".gz" if compressed else ""
    return UPLOADS_DIR / sha256[:2] / sha256[2:4] / (sha256[4:] + suffix)


def store_file(raw_bytes: bytes, mime: str) -> dict:
    """
    Persist bytes to disk (deduped + optionally compressed).
    Returns metadata dict to save in DB.
    """
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    compress = _should_compress(mime)
    path = _hash_path(sha256, compress)

    original_size = len(raw_bytes)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        if compress:
            stored_bytes = gzip.compress(raw_bytes, compresslevel=9)
        else:
            stored_bytes = raw_bytes
        path.write_bytes(stored_bytes)
        stored_size = len(stored_bytes)
    else:
        stored_size = path.stat().st_size

    return {
        "sha256":        sha256,
        "compressed":    compress,
        "original_size": original_size,
        "stored_size":   stored_size,
        "path":          str(path),
    }


def read_file(sha256: str, compressed: bool) -> bytes:
    """Read raw (decompressed) bytes for a stored file."""
    path = _hash_path(sha256, compressed)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {sha256}")
    data = path.read_bytes()
    return gzip.decompress(data) if compressed else data


def delete_file_if_unreferenced(sha256: str, compressed: bool):
    """Remove from disk. Call only when ref_count reaches 0."""
    path = _hash_path(sha256, compressed)
    if path.exists():
        path.unlink()
        # Clean up empty parent dirs
        for parent in [path.parent, path.parent.parent]:
            try:
                parent.rmdir()
            except OSError:
                pass


def guess_mime(filename: str, fallback="application/octet-stream") -> str:
    return mimetypes.guess_type(filename)[0] or fallback