"""
python-services/shared/schemas.py

Cross-service wire schemas (Pydantic v2). Every Kafka topic and HTTP
boundary uses one of these; JSON serialisation round-trips losslessly.

Topics and their envelope models:
    detections               → DetectionMessage  (C++ → violation-service)
    snapshot_requests        → SnapshotRequest   (violation → DeepStream)
    snapshot_responses       → SnapshotResponse  (DeepStream → snapshot-consumer)
    ocr_requests             → OCRRequest        (violation → plate-service)
    ocr_results              → OCRResult         (plate → violation)
    penalty_card_requests    → PenaltyCardRequest (violation → penalty-card)
    violations               → ViolationEvent    (audit/stream for frontend)

API request / response DTOs live alongside the Kafka envelopes so
FastAPI routers reuse them without duplicating definitions.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #
class BBoxModel(BaseModel):
    """Image-space bounding box."""

    model_config = ConfigDict(frozen=True)

    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    w: int = Field(..., gt=0)
    h: int = Field(..., gt=0)


class Point2D(BaseModel):
    model_config = ConfigDict(frozen=True)

    x: int
    y: int


class VehicleClass(str, Enum):
    CAR = "car"
    MOTORCYCLE = "motorcycle"
    BUS = "bus"
    TRUCK = "truck"
    OTHER = "other"


class ZoneType(str, Enum):
    NO_PARKING = "no_parking"
    TOW_AWAY = "tow_away"
    RESTRICTED = "restricted"


class ViolationStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DISMISSED = "dismissed"
    DISPUTED = "disputed"


class CVIState(str, Enum):
    OUTSIDE = "OUTSIDE"
    INSIDE_ZONE = "INSIDE_ZONE"
    VIOLATED = "VIOLATED"
    EXIT_CANDIDATE = "EXIT_CANDIDATE"
    COOLDOWN = "COOLDOWN"


# --------------------------------------------------------------------------- #
# C++ DeepStream → violation-service
# --------------------------------------------------------------------------- #
class DetectedObject(BaseModel):
    """A single object detected in a frame — spec §7.2."""

    ds_track_id: int
    class_id: int
    class_name: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    bbox: BBoxModel
    centroid: Point2D
    embedding_b64: str | None = None    # 128-D OSNet feature (kz_new §8 onwards)


class DetectionMessage(BaseModel):
    """Kafka envelope for topic `detections`."""

    sensor_id: str
    timestamp: datetime
    frame_id: int
    objects: list[DetectedObject]

    @field_validator("timestamp", mode="before")
    @classmethod
    def _coerce_ts(cls, v: Any) -> Any:
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


# --------------------------------------------------------------------------- #
# Snapshot round-trip
# --------------------------------------------------------------------------- #
class SnapshotType(str, Enum):
    VEHICLE_CROP = "vehicle_crop"
    FULL_FRAME = "full_frame"
    PLATE_REGION = "plate_region"


class SnapshotRequest(BaseModel):
    """Kafka envelope for topic `snapshot_requests`."""

    request_id: UUID
    camera_id: str
    frame_id: int | None = None
    reason: str
    type: SnapshotType
    bbox: BBoxModel | None = None


class SnapshotResponse(BaseModel):
    """Kafka envelope for topic `snapshot_responses`."""

    request_id: UUID
    camera_id: str
    timestamp: datetime
    path: str                  # container-local path before MinIO upload
    minio_key: str | None = None
    url: str | None = None     # populated by snapshot-consumer after upload


# --------------------------------------------------------------------------- #
# OCR round-trip
# --------------------------------------------------------------------------- #
class OCRRequest(BaseModel):
    """Kafka envelope for topic `ocr_requests`."""

    request_id: UUID
    cvi_id: UUID
    camera_id: str
    attempt_no: int = Field(..., ge=1)
    vehicle_crop_b64: str


class OCRResult(BaseModel):
    """Kafka envelope for topic `ocr_results`."""

    request_id: UUID
    cvi_id: UUID
    camera_id: str
    plate_text: str | None
    plate_confidence: float = Field(..., ge=0.0, le=1.0)
    plate_crop_b64: str | None = None
    region_name: str | None = None   # 'kz', 'kz_box', ...
    format_type: Literal["kz_new", "kz_old", "kz_diplo", "unknown"] = "unknown"
    region_code: str | None = None
    valid_format: bool = False
    is_diplomatic: bool = False


# --------------------------------------------------------------------------- #
# Penalty card
# --------------------------------------------------------------------------- #
class PenaltyCardRequest(BaseModel):
    """Kafka envelope for topic `penalty_card_requests`."""

    violation_id: int
    snapshot_request_id: UUID | None = None


# --------------------------------------------------------------------------- #
# Violation stream (frontend/WebSocket)
# --------------------------------------------------------------------------- #
class ViolationEvent(BaseModel):
    """WS push payload + audit stream."""

    violation_id: int
    camera_id: str
    zone_id: int
    cvi_id: UUID
    plate_text: str | None
    plate_format: Literal["kz_new", "kz_old", "kz_diplo", "unknown"]
    plate_region_code: str | None
    vehicle_class: VehicleClass
    violation_time: datetime
    duration_seconds: int | None = None
    snapshot_vehicle_url: str | None = None
    snapshot_plate_url: str | None = None


# --------------------------------------------------------------------------- #
# REST DTOs — cameras
# --------------------------------------------------------------------------- #
class CameraBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    rtsp_substream_url: str = Field(..., min_length=1)
    rtsp_mainstream_url: str | None = None
    location_description: str | None = None
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class CameraCreate(CameraBase):
    id: str = Field(..., min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_\-]+$")


class CameraUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    rtsp_substream_url: str | None = None
    rtsp_mainstream_url: str | None = None
    location_description: str | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None


class CameraOut(CameraBase):
    id: str
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# REST DTOs — zones
# --------------------------------------------------------------------------- #
class PolygonPoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    x: float
    y: float


class ZoneBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    zone_type: ZoneType = ZoneType.NO_PARKING
    polygon: list[PolygonPoint] = Field(..., min_length=3)
    threshold_seconds: int = Field(20, ge=1, le=3600)
    exit_confirm_seconds: int = Field(10, ge=1, le=300)
    cooldown_seconds: int = Field(10, ge=1, le=300)
    color_hex: str = Field("#FF0000", pattern=r"^#[0-9A-Fa-f]{6}$")
    enabled: bool = True


class ZoneCreate(ZoneBase):
    pass


class ZoneUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    zone_type: ZoneType | None = None
    polygon: list[PolygonPoint] | None = Field(None, min_length=3)
    threshold_seconds: int | None = Field(None, ge=1, le=3600)
    exit_confirm_seconds: int | None = Field(None, ge=1, le=300)
    cooldown_seconds: int | None = Field(None, ge=1, le=300)
    color_hex: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    enabled: bool | None = None


class ZoneOut(ZoneBase):
    id: int
    camera_id: str
    created_by: str | None
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# REST DTOs — violations
# --------------------------------------------------------------------------- #
class ViolationOut(BaseModel):
    id: int
    camera_id: str
    zone_id: int
    cvi_id: UUID | None
    plate_text: str | None
    plate_format: str | None
    plate_region_code: str | None
    plate_confidence: float | None
    vehicle_class: str | None
    first_seen_in_zone: datetime
    violation_time: datetime
    exit_confirmed_time: datetime | None
    duration_seconds: int | None
    snapshot_vehicle_url: str | None
    snapshot_plate_url: str | None
    status: ViolationStatus
    reviewed_by: str | None
    reviewed_at: datetime | None
    penalty_card_url: str | None
    created_at: datetime


class ViolationPatch(BaseModel):
    status: ViolationStatus


class ViolationDispute(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
class HealthStatus(BaseModel):
    status: Literal["ok", "degraded", "down"]
    version: str
    service: str
    checks: dict[str, bool]
    timestamp: datetime


__all__ = [
    "BBoxModel",
    "CVIState",
    "CameraBase",
    "CameraCreate",
    "CameraOut",
    "CameraUpdate",
    "DetectedObject",
    "DetectionMessage",
    "HealthStatus",
    "OCRRequest",
    "OCRResult",
    "PenaltyCardRequest",
    "Point2D",
    "PolygonPoint",
    "SnapshotRequest",
    "SnapshotResponse",
    "SnapshotType",
    "VehicleClass",
    "ViolationDispute",
    "ViolationEvent",
    "ViolationOut",
    "ViolationPatch",
    "ViolationStatus",
    "ZoneBase",
    "ZoneCreate",
    "ZoneOut",
    "ZoneType",
    "ZoneUpdate",
]
