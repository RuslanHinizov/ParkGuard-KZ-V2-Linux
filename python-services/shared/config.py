"""
python-services/shared/config.py

Centralised runtime configuration (spec §4 kickoff rule: magic numbers banned).
Single Settings class, loaded from environment variables; Pydantic v2 validates
and exposes typed attributes to every service.

Import pattern:
    from shared.config import settings
    settings.DATABASE_URL  # fully typed
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings — populated from environment (.env)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # -------- General --------
    ENV: Literal["dev", "staging", "prod"] = "dev"
    PROJECT_NAME: str = "parkguard-kz"
    TZ: str = "Asia/Almaty"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    DEFAULT_OPERATOR_NAME: str = "System"

    # -------- Postgres --------
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "parkguard"
    POSTGRES_USER: str = "parkguard"
    POSTGRES_PASSWORD: str = Field(..., min_length=1)
    DATABASE_URL: PostgresDsn
    DATABASE_URL_SYNC: PostgresDsn

    # -------- Redis --------
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = Field(..., min_length=1)
    REDIS_URL: RedisDsn

    # -------- Kafka --------
    KAFKA_BOOTSTRAP_SERVERS: str = "kafka:9092"
    KAFKA_CLIENT_ID: str = "parkguard"
    KAFKA_CONSUMER_GROUP_VIOLATION: str = "violation-service"
    KAFKA_CONSUMER_GROUP_PLATE: str = "plate-service"
    KAFKA_CONSUMER_GROUP_SNAPSHOT: str = "snapshot-consumer"
    KAFKA_CONSUMER_GROUP_PENALTY: str = "penalty-card-service"

    KAFKA_TOPIC_DETECTIONS: str = "detections"
    KAFKA_TOPIC_VIOLATIONS: str = "violations"
    KAFKA_TOPIC_SNAPSHOT_REQ: str = "snapshot_requests"
    KAFKA_TOPIC_SNAPSHOT_RES: str = "snapshot_responses"
    KAFKA_TOPIC_OCR_REQ: str = "ocr_requests"
    KAFKA_TOPIC_OCR_RES: str = "ocr_results"
    KAFKA_TOPIC_PENALTY_REQ: str = "penalty_card_requests"

    # -------- MinIO --------
    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_ROOT_USER: str = "parkguard"
    MINIO_ROOT_PASSWORD: str = Field(..., min_length=1)
    MINIO_BUCKET_SNAPSHOTS: str = "snapshots"
    MINIO_BUCKET_CARDS: str = "penalty-cards"
    MINIO_USE_SSL: bool = False
    MINIO_PRESIGN_TTL_SECONDS: int = 900

    # -------- Event API --------
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_CORS_ORIGINS: str = "http://localhost:5173,http://dashboard.local"
    API_RATE_LIMIT_PER_MIN: int = 500

    # -------- Plate service --------
    PLATE_SERVICE_DEVICE: Literal["cuda", "cpu"] = "cuda"
    PLATE_SERVICE_BATCH_SIZE: int = 4
    PLATE_SERVICE_CONFIDENCE_THRESHOLD: float = 0.5
    PLATE_SERVICE_ACCEPTED_REGIONS: str = "kz,kz_box"
    PLATE_SERVICE_WARMUP_IMAGES: int = 5

    # -------- Violation thresholds (spec §1.1, §5) --------
    VIOLATION_DEFAULT_THRESHOLD_SECONDS: int = 20
    VIOLATION_DEFAULT_EXIT_CONFIRM_SECONDS: int = 10
    VIOLATION_DEFAULT_COOLDOWN_SECONDS: int = 10
    VIOLATION_OCR_ATTEMPT_INTERVAL_SECONDS: int = 5
    VIOLATION_PLATE_VOTE_THRESHOLD: int = 3
    VIOLATION_EMBEDDING_MATCH_THRESHOLD: float = 0.82
    VIOLATION_SPATIAL_IOU_THRESHOLD: float = 0.3
    VIOLATION_CVI_INACTIVE_TTL_SECONDS: int = 600     # 10 min
    VIOLATION_ACTIVE_REGISTRY_TTL_SECONDS: int = 3600  # 1 h

    # -------- Observability --------
    PROMETHEUS_PORT: int = 9090
    GRAFANA_PORT: int = 3000
    LOKI_PORT: int = 3100
    ALERTMANAGER_PORT: int = 9093

    # -------- Validators --------
    @field_validator("API_CORS_ORIGINS")
    @classmethod
    def _strip_origins(cls, v: str) -> str:
        return ",".join(origin.strip() for origin in v.split(",") if origin.strip())

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS origins as list (FastAPI expects list[str])."""
        return [o for o in self.API_CORS_ORIGINS.split(",") if o]

    @property
    def accepted_plate_regions(self) -> set[str]:
        return {r.strip() for r in self.PLATE_SERVICE_ACCEPTED_REGIONS.split(",") if r.strip()}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton — avoid repeat .env parsing."""
    return Settings()  # type: ignore[call-arg]


settings: Settings = get_settings()
