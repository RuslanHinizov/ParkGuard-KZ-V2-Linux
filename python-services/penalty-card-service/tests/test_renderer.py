"""
python-services/penalty-card-service/tests/test_renderer.py

Unit tests for PenaltyCardRenderer.  WeasyPrint needs Pango/Cairo which
are not available on Windows dev boxes (they're installed in the Ubuntu
container). Tests are therefore guarded with pytest.importorskip so the
suite stays green on Windows — the import fails gracefully and tests are
skipped instead of erroring.

The skip covers: weasyprint, qrcode (may or may not be present on CI).
On Ubuntu (where the container runs) both libraries are present and the
tests exercise real PDF generation end-to-end.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

# --------------------------------------------------------------------------- #
# Conditional skip
# --------------------------------------------------------------------------- #
weasyprint   = pytest.importorskip("weasyprint",   reason="WeasyPrint not installed")
qrcode_mod   = pytest.importorskip("qrcode",       reason="qrcode not installed")
jinja2_mod   = pytest.importorskip("jinja2",       reason="Jinja2 not installed")

from src.renderer import CardData, PenaltyCardRenderer  # noqa: E402 — after skip guard


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def renderer() -> PenaltyCardRenderer:
    return PenaltyCardRenderer(review_base_url="http://dashboard.local/violations")


def _make_card(**overrides) -> CardData:
    defaults = dict(
        violation_id=42,
        camera_id="cam_01",
        zone_id=7,
        zone_name="North Parking",
        violation_time=datetime(2026, 4, 17, 10, 23, 45, tzinfo=timezone.utc),
        duration_seconds=185,
        plate_text="123ABC01",
        plate_format="kz_new",
        plate_region_code="01",
        is_diplomatic=False,
        vehicle_class="car",
        snapshot_b64=None,
    )
    defaults.update(overrides)
    return CardData(**defaults)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_render_returns_pdf_bytes(renderer: PenaltyCardRenderer) -> None:
    pdf = renderer.render_pdf(_make_card())
    assert isinstance(pdf, bytes)
    assert len(pdf) > 1000, "PDF too small — likely empty"
    # PDF magic bytes
    assert pdf[:4] == b"%PDF", "Output does not start with PDF magic"


def test_render_unknown_plate(renderer: PenaltyCardRenderer) -> None:
    """Card generation must succeed when plate_text is None."""
    pdf = renderer.render_pdf(_make_card(plate_text=None))
    assert pdf[:4] == b"%PDF"


def test_render_with_snapshot(renderer: PenaltyCardRenderer) -> None:
    """A minimal 1×1 white JPEG embeds without crashing WeasyPrint."""
    # Minimal valid JPEG (1×1 white pixel)
    tiny_jpeg_b64 = (
        "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8U"
        "HRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgN"
        "DRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
        "MjL/wAARCAABAAEDASIAAhEBAxEB/8QAFgABAQEAAAAAAAAAAAAAAAAABgUE/8QAIBAAAgIB"
        "BQEAAAAAAAAAAAAAAAECBAUGIRJBQ//EABUBAQEAAAAAAAAAAAAAAAAAAAAB/8QAFBEBAAAA"
        "AAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AKy1FiSulJGNvbGvRalWwAFSqSCAf//Z"
    )
    pdf = renderer.render_pdf(_make_card(snapshot_b64=tiny_jpeg_b64))
    assert pdf[:4] == b"%PDF"


def test_render_diplomatic(renderer: PenaltyCardRenderer) -> None:
    """Diplomatic flag renders without error."""
    pdf = renderer.render_pdf(
        _make_card(is_diplomatic=True, plate_text="CD001KZ", plate_format="kz_diplo")
    )
    assert pdf[:4] == b"%PDF"


def test_render_no_duration(renderer: PenaltyCardRenderer) -> None:
    pdf = renderer.render_pdf(_make_card(duration_seconds=None))
    assert pdf[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_render_pdf_async(renderer: PenaltyCardRenderer) -> None:
    pdf = await renderer.render_pdf_async(_make_card())
    assert pdf[:4] == b"%PDF"


