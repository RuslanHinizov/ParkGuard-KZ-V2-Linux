"""
python-services/penalty-card-service/src/renderer.py

Adım 9 (spec §11). Produces a bilingual (KZ/RU/EN) A4 PDF penalty card for a
parking violation using:

  • Jinja2 for HTML templating
  • qrcode for the embedded QR pointing to the operator review URL
  • WeasyPrint for HTML→PDF conversion

All I/O (DB reads, MinIO fetches) happens in the caller (main.py). The
renderer receives a :class:`CardData` plain-dataclass and returns raw PDF
bytes — making it trivially unit-testable without any running services.

Design notes
------------
* WeasyPrint is CPU-bound and can block the event loop for 300-900 ms;
  `render_pdf_async` wraps it in `asyncio.to_thread` (spec §15.9).
* The QR payload is the LAN review URL constructed from `REVIEW_BASE_URL`
  so operators can scan the card on-site without internet access.
* The HTML template lives in `src/templates/card.html` and is loaded once
  at class-init time using Jinja2's FileSystemLoader.
"""

from __future__ import annotations

import asyncio
import base64
import io
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import qrcode
import qrcode.image.svg
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

_TEMPLATE_DIR = Path(__file__).parent / "templates"

# Base URL the QR code points to.  Override via env for LAN deployments.
_DEFAULT_REVIEW_BASE = "http://dashboard.local/violations"


@dataclass(slots=True)
class CardData:
    """Everything the renderer needs to produce the PDF card."""

    violation_id: int
    camera_id: str
    zone_id: int
    zone_name: str
    violation_time: datetime
    duration_seconds: int | None
    plate_text: str | None
    plate_format: str                    # kz_new | kz_old | kz_diplo | unknown
    plate_region_code: str | None
    is_diplomatic: bool
    vehicle_class: str | None
    snapshot_b64: str | None             # base64 JPEG vehicle snapshot (optional)
    extra: dict[str, Any] = field(default_factory=dict)


class PenaltyCardRenderer:
    """
    Stateless renderer — safe to share across Kafka handler invocations.
    Instantiate once at startup (``__init__`` compiles the Jinja template).
    """

    def __init__(
        self,
        *,
        review_base_url: str = _DEFAULT_REVIEW_BASE,
        template_dir: Path | None = None,
    ) -> None:
        self._review_base = review_base_url.rstrip("/")
        tdir = template_dir or _TEMPLATE_DIR
        self._env = Environment(
            loader=FileSystemLoader(str(tdir)),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._tmpl = self._env.get_template("card.html")

    # ------------------------------------------------------------------ public
    def render_pdf(self, data: CardData) -> bytes:
        """
        Render synchronously. Callers on the async path should use
        :meth:`render_pdf_async` to avoid blocking the event loop.
        """
        qr_b64  = _qr_to_base64(f"{self._review_base}/{data.violation_id}/review")
        html_src = self._tmpl.render(
            v=data,
            qr_b64=qr_b64,
            generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        )
        pdf_bytes: bytes = HTML(string=html_src, base_url=str(_TEMPLATE_DIR)).write_pdf()
        return pdf_bytes

    async def render_pdf_async(self, data: CardData) -> bytes:
        """Off-thread wrapper so WeasyPrint doesn't block the event loop."""
        return await asyncio.to_thread(self.render_pdf, data)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _qr_to_base64(url: str) -> str:
    """Generate a QR code PNG and return it as a data-URI base64 string."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


__all__ = ["CardData", "PenaltyCardRenderer"]
