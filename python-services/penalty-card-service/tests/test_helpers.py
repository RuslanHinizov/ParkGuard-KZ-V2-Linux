"""
python-services/penalty-card-service/tests/test_helpers.py

Pure-function helpers in src/card_utils.py — no rendering or I/O deps.
Run on any platform (Windows dev box included).
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.card_utils import pdf_key, url_to_minio_key


def test_pdf_key_format() -> None:
    ts = datetime(2026, 4, 17, 10, 0, 0, tzinfo=timezone.utc)
    assert pdf_key("cam_01", ts, 99) == "cam_01/2026/04/17/99.pdf"


def test_pdf_key_sanitises_camera_id() -> None:
    ts = datetime(2026, 4, 17, 0, 0, 0, tzinfo=timezone.utc)
    key = pdf_key("cam/bad id", ts, 1)
    assert "cam_bad_id" in key
    assert key.endswith("/2026/04/17/1.pdf")


def test_url_to_minio_key_standard() -> None:
    url = "http://minio:9000/penalty-cards/cam_01/2026/04/17/99.pdf"
    assert url_to_minio_key(url) == "cam_01/2026/04/17/99.pdf"


def test_url_to_minio_key_passthrough_on_short() -> None:
    raw = "cam_01/2026/04/17/99.pdf"
    assert url_to_minio_key(raw) == raw
