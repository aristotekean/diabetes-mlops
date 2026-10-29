"""HTTP client for the Inference API, the only backend the UI talks to."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests

TIMEOUT_SECONDS = 10


@dataclass
class ApiError(Exception):
    """A failed call, with per-field messages when the API rejected the input."""

    message: str
    status_code: int | None = None
    request_id: str | None = None
    field_errors: list[tuple[str, str]] = field(default_factory=list)

    def __str__(self) -> str:
        return self.message


def _field_path(loc: list[Any]) -> str:
    # FastAPI locations look like ["body", "features", "age", "str"]; the
    # trailing type tag only says which union member failed.
    parts = [str(part) for part in loc if part not in ("body", "features")]
    if len(parts) > 1 and parts[-1] in ("str", "int", "float"):
        parts = parts[:-1]
    return ".".join(parts) or "body"


def parse_validation_errors(detail: Any) -> list[tuple[str, str]]:
    """Collapse FastAPI 422 details into unique ``(field, message)`` pairs."""
    if not isinstance(detail, list):
        return []
    seen: dict[str, str] = {}
    for error in detail:
        if not isinstance(error, dict):
            continue
        path = _field_path(list(error.get("loc", ())))
        seen.setdefault(path, str(error.get("msg", "invalid value")))
    return list(seen.items())


class ApiClient:
    def __init__(self, base_url: str, session: requests.Session | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()

    def _get(self, path: str) -> dict[str, Any]:
        try:
            response = self._session.get(f"{self.base_url}{path}", timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ApiError(f"GET {path} falló: {exc}") from exc
        return response.json()

    def health(self) -> dict[str, Any]:
        return self._get("/health")

    def model_info(self) -> dict[str, Any]:
        return self._get("/model-info")

    def predict(self, features: dict[str, Any]) -> dict[str, Any]:
        return self.predict_raw({"features": features})

    def predict_raw(self, body: Any) -> dict[str, Any]:
        """POST an arbitrary body, so the UI can also show the API's validation."""
        try:
            response = self._session.post(
                f"{self.base_url}/predict", json=body, timeout=TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            raise ApiError(f"No se pudo contactar la API: {exc}") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code == 200:
            return payload
        request_id = payload.get("request_id") if isinstance(payload, dict) else None
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if response.status_code == 422:
            raise ApiError(
                "La API rechazó los datos de entrada.",
                status_code=422,
                request_id=request_id,
                field_errors=parse_validation_errors(detail),
            )
        message = detail.get("message") if isinstance(detail, dict) else None
        raise ApiError(
            message or f"La API respondió HTTP {response.status_code}.",
            status_code=response.status_code,
            request_id=request_id,
        )
