"""Pydantic models for the HTTP contract (see docs/data-contract.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]
    model_loaded: bool
    database: Literal["ok", "error"]


class ModelInfoResponse(BaseModel):
    name: str
    alias: str
    status: Literal["ready", "unavailable"]
    version: str | None
    run_id: str | None
    loaded_at: datetime | None
    last_check_at: datetime | None
    last_error: str | None
    refresh_seconds: float
    classes: list[str] | None
    tracking_uri: str | None


class ReloadResponse(BaseModel):
    previous_version: str | None
    current_version: str
    changed: bool


class ErrorDetail(BaseModel):
    error_code: str
    message: str
