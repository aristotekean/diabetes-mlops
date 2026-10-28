"""POST /predict: validate, infer in a worker thread, persist one log row."""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api_inference.model import ModelError, ModelNotLoadedError, ModelService
from api_inference.repository import InferenceLogRecord, InferenceLogRepository
from api_inference.schemas import ModelIdentity, PredictRequest, PredictResponse

logger = logging.getLogger(__name__)

PREDICT_PATH = "/predict"
_MAX_RAW_BODY = 2_000
_MAX_ERROR_MESSAGE = 2_000


def _error_response(
    status_code: int, request_id: UUID, error_code: str, message: str
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "request_id": str(request_id),
            "detail": {"error_code": error_code, "message": message},
        },
    )


async def _safe_log(repository: InferenceLogRepository, record: InferenceLogRecord) -> bool:
    """Persist the row; never let a logging failure break the response."""
    try:
        await repository.log(record)
        return True
    except Exception:
        logger.exception("inference log write failed for request %s", record.request_id)
        return False


def input_data_from_body(body: Any) -> dict[str, Any]:
    """Best-effort JSON object for ``inference.logs.input_data`` on bad input."""
    if isinstance(body, dict):
        features = body.get("features")
        return features if isinstance(features, dict) else body
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    return {"raw": str(body)[:_MAX_RAW_BODY]}


def request_id_from_body(body: Any) -> UUID:
    if isinstance(body, dict):
        try:
            return UUID(str(body.get("request_id")))
        except (ValueError, TypeError, AttributeError):
            pass
    return uuid4()


def build_router(service: ModelService, repository: InferenceLogRepository) -> APIRouter:
    router = APIRouter(tags=["inference"])

    @router.post(PREDICT_PATH, response_model=PredictResponse)
    async def predict(payload: PredictRequest, request: Request) -> Any:
        request_id = payload.request_id or uuid4()
        # The exact submitted object is what the audit log stores; the
        # validated copy (same keys) is what the model receives.
        raw_body = await request.json()
        submitted = raw_body["features"]
        started = time.perf_counter()

        try:
            result = await service.predict(payload.feature_values())
        except ModelNotLoadedError as exc:
            await _safe_log(
                repository,
                InferenceLogRecord(
                    request_id=request_id,
                    input_data=submitted,
                    status="failed",
                    model_name=service.name,
                    model_alias=service.alias,
                    error_code="model_unavailable",
                    error_message=str(exc),
                ),
            )
            return _error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE, request_id, "model_unavailable", str(exc)
            )
        except Exception as exc:
            loaded = service.loaded
            logger.exception("inference failed for request %s", request_id)
            await _safe_log(
                repository,
                InferenceLogRecord(
                    request_id=request_id,
                    input_data=submitted,
                    status="failed",
                    model_name=loaded.name if loaded else service.name,
                    model_version=loaded.version if loaded else None,
                    model_alias=loaded.alias if loaded else service.alias,
                    error_code="inference_error",
                    error_message=f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_MESSAGE],
                ),
            )
            return _error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                request_id,
                "inference_error",
                "the model failed to score this observation",
            )

        processing_time_ms = (time.perf_counter() - started) * 1000
        record = InferenceLogRecord(
            request_id=request_id,
            input_data=submitted,
            status="succeeded",
            prediction=result.prediction,
            probabilities=result.probabilities,
            model_name=result.model.name,
            model_version=result.model.version,
            model_alias=result.model.alias,
            processing_time_ms=processing_time_ms,
        )
        await _safe_log(repository, record)
        return PredictResponse(
            request_id=request_id,
            prediction=result.prediction,
            probabilities=result.probabilities,
            model=ModelIdentity(
                name=result.model.name, version=result.model.version, alias=result.model.alias
            ),
            processing_time_ms=processing_time_ms,
        )

    return router


def build_validation_handler(repository: InferenceLogRepository):
    """422 handler that also writes a ``failed`` row for /predict requests."""

    async def handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = jsonable_encoder(exc.errors(), exclude={"input", "url", "ctx"})
        content: dict[str, Any] = {"detail": errors}
        if request.url.path == PREDICT_PATH:
            request_id = request_id_from_body(exc.body)
            message = "; ".join(
                f"{'.'.join(str(part) for part in error.get('loc', ()))}: {error.get('msg')}"
                for error in errors
            )
            await _safe_log(
                repository,
                InferenceLogRecord(
                    request_id=request_id,
                    input_data=input_data_from_body(exc.body),
                    status="failed",
                    error_code="validation_error",
                    error_message=message[:_MAX_ERROR_MESSAGE],
                ),
            )
            content["request_id"] = str(request_id)
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content=content)

    return handler
