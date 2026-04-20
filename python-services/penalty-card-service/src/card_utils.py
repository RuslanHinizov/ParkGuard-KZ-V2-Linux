"""
python-services/penalty-card-service/src/card_utils.py

Pure utility functions with no heavy dependencies — importable in tests
without triggering WeasyPrint / qrcode imports.
"""

from __future__ import annotations

from datetime import datetime


def pdf_key(camera_id: str, ts: datetime, violation_id: int) -> str:
    """MinIO object key: <cam>/<YYYY>/<MM>/<DD>/<violation_id>.pdf"""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in camera_id) or "cam"
    return f"{safe}/{ts.year:04d}/{ts.month:02d}/{ts.day:02d}/{violation_id}.pdf"


def url_to_minio_key(url: str) -> str:
    """
    Strip the ``http://<host>/<bucket>/`` prefix from a MinIO URL to get
    the bare object key.  Returns `url` unchanged if it has no scheme
    (already a bare key) or cannot be parsed.

    Expected form: ``http://minio:9000/<bucket>/<key…>``
    """
    try:
        if "://" not in url:
            return url          # already a bare key
        # e.g. http://minio:9000/penalty-cards/cam_01/2026/04/17/99.pdf
        # split: ['http:', '', 'minio:9000', 'penalty-cards', 'cam_01/…']
        parts = url.split("/", 4)
        return parts[4] if len(parts) >= 5 else url
    except Exception:  # noqa: BLE001
        return url


__all__ = ["pdf_key", "url_to_minio_key"]
