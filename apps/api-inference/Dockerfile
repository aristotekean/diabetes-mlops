# Inference API (S4) image.
#
# Dependencies are installed from apps/api-inference/pyproject.toml + uv.lock,
# so the runtime resolves exactly the versions pinned in the lockfile.
#
# Build context is the repository root, so the shared ml/diabetes_model package
# can be copied in once the API depends on it:
#   docker compose build api-inference
#   docker build -f apps/api-inference/Dockerfile -t api-inference .

# --------------------------------------------------------------------------- #
# Dependencies (venv built with uv, no project install)
# --------------------------------------------------------------------------- #
FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim AS deps

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app
COPY apps/api-inference/pyproject.toml apps/api-inference/uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

# --------------------------------------------------------------------------- #
# Runtime
# --------------------------------------------------------------------------- #
FROM python:3.14-slim-trixie AS api

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src"

RUN groupadd -g 1000 app && useradd -u 1000 -g 1000 -m -s /bin/bash app

WORKDIR /app
COPY --from=deps /app/.venv /app/.venv
COPY apps/api-inference/src/ /app/src/

RUN chown -R 1000:1000 /app

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["uvicorn", "api_inference.main:app", "--host", "0.0.0.0", "--port", "8000"]
