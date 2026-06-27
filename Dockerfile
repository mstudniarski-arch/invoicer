# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    PYTHONPATH=/app/src \
    PORT=8080

# uv (pinned via setup-uv image stage)
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# 1) Dependency layer (cache-friendly)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) App source
COPY src ./src

# 2b) Korpus prawny + skrypty (release-command ingest do pgvector)
COPY data ./data
COPY scripts ./scripts

# 3) Runtime
EXPOSE 8080
# --no-sync: uzyj gotowego .venv z warstwy build; NIE synchronizuj dev-deps przy starcie (offline-safe)
CMD ["uv", "run", "--no-sync", "uvicorn", "invoicer.app:_factory", "--factory", "--host", "0.0.0.0", "--port", "8080"]
