"""
python-services/violation-service/src/models/__init__.py

Re-exports ORM classes so `from src.models import Violation, CVIRecord`
works from any module without reaching into submodules.
"""

from __future__ import annotations

from src.models.cvi import CVIRecord
from src.models.violation import Violation

__all__ = ["CVIRecord", "Violation"]
