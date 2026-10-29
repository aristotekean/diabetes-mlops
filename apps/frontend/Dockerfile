# Frontend (S7) image: Streamlit UI that talks to the Inference API only.
#
# Dependencies are installed from apps/frontend/pyproject.toml + uv.lock.
# Build context is the repository root, consistent with the other services:
#   docker compose build frontend
#   docker build -f apps/frontend/Dockerfile -t frontend .

# --------------------------------------------------------------------------- #
# Dependencies (venv built with uv, no project install)
# --------------------------------------------------------------------------- #
FROM ghcr.io/astral-sh/uv:python3.15-trixie-slim AS deps

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app
COPY apps/frontend/pyproject.toml apps/frontend/uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

# --------------------------------------------------------------------------- #
# Runtime
# --------------------------------------------------------------------------- #
FROM python:3.15-slim-trixie AS frontend

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

RUN groupadd -g 1000 app && useradd -u 1000 -g 1000 -m -s /bin/bash app

WORKDIR /app
COPY --from=deps /app/.venv /app/.venv
COPY apps/frontend/src/ /app/src/

RUN chown -R 1000:1000 /app

USER app
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4)"

CMD ["streamlit", "run", "src/frontend/app.py", "--server.address", "0.0.0.0", "--server.port", "8501"]
