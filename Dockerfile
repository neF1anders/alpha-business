# One backend image for every Python service (ingestion, orchestrator with retrieval and summary branch):
# torch and the other heavy dependencies are installed once. Services differ only by command (compose.yaml).
FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

FROM python:3.12-slim-bookworm AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
# Dependencies first: this layer is rebuilt only when pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra ml --no-install-project
COPY src/backend ./src/backend
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra ml

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# The project is installed editable: packages stay in src/backend, sql/ is found next to them.
COPY --from=builder /app /app
COPY sql ./sql
COPY scripts ./scripts
COPY tests ./tests
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 OMP_THREAD_LIMIT=2 HOST=0.0.0.0 PORT=8000 \
    COMPANY_TAGS_PATH=/app/data/processed/company_tags.csv BANNER_STORE_PATH=/app/data/cache/banners.db
EXPOSE 8000
CMD ["python", "-m", "orchestrator"]
