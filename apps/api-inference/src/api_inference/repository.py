"""Persistence of one ``inference.logs`` row per request."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InferenceLogRecord:
    """Mirror of the ``inference.logs`` columns written by the API."""

    request_id: UUID
    input_data: dict[str, Any]
    status: str  # "succeeded" | "failed"
    prediction: str | None = None
    probabilities: dict[str, float] | None = None
    model_name: str | None = None
    model_version: str | None = None
    model_alias: str | None = None
    processing_time_ms: float | None = None
    error_code: str | None = None
    error_message: str | None = None


class InferenceLogRepository(Protocol):
    async def open(self) -> None: ...

    async def close(self) -> None: ...

    async def ping(self) -> None: ...

    async def log(self, record: InferenceLogRecord) -> None: ...


_INSERT = """
INSERT INTO inference.logs (
    request_id, input_data, prediction, probabilities,
    model_name, model_version, model_alias, processing_time_ms,
    status, error_code, error_message
) VALUES (
    %(request_id)s, %(input_data)s, %(prediction)s, %(probabilities)s,
    %(model_name)s, %(model_version)s, %(model_alias)s, %(processing_time_ms)s,
    %(status)s, %(error_code)s, %(error_message)s
)
"""


class PostgresInferenceLogRepository:
    """Async psycopg3 pool against the training database (``postgres-train``)."""

    def __init__(
        self,
        dsn: Callable[[], str],
        *,
        min_size: int = 1,
        max_size: int = 4,
        connect_timeout_seconds: float = 5.0,
    ) -> None:
        # The DSN is resolved lazily so importing the app never requires the
        # database environment; a missing configuration fails at startup.
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._timeout = connect_timeout_seconds
        self._pool: AsyncConnectionPool | None = None

    async def open(self) -> None:
        self._pool = AsyncConnectionPool(
            self._dsn(),
            min_size=self._min_size,
            max_size=self._max_size,
            open=False,
            timeout=self._timeout,
            kwargs={"connect_timeout": int(self._timeout)},
        )
        # Do not wait for the first connection: a slow database must not keep
        # the API from starting. ``/health`` reports the real state.
        await self._pool.open(wait=False)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    def _require_pool(self) -> AsyncConnectionPool:
        if self._pool is None:
            raise RuntimeError("repository is not open")
        return self._pool

    async def ping(self) -> None:
        async with self._require_pool().connection() as conn:
            await conn.execute("SELECT 1")

    async def log(self, record: InferenceLogRecord) -> None:
        params = {
            "request_id": record.request_id,
            "input_data": Jsonb(record.input_data),
            # ``prediction`` is a JSONB column: store the label as a JSON string.
            "prediction": Jsonb(record.prediction) if record.prediction is not None else None,
            "probabilities": Jsonb(record.probabilities)
            if record.probabilities is not None
            else None,
            "model_name": record.model_name,
            "model_version": record.model_version,
            "model_alias": record.model_alias,
            "processing_time_ms": record.processing_time_ms,
            "status": record.status,
            "error_code": record.error_code,
            "error_message": record.error_message,
        }
        async with self._require_pool().connection() as conn:
            await conn.execute(_INSERT, params)
