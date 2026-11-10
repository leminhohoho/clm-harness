# build context: repo root
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_LINK_MODE=copy PYTHONUNBUFFERED=1 DATA_DIR=/data

# harness library: baked at /opt/harness-lib, injected into agent runs via PYTHONPATH
COPY lib /opt/harness-lib
# backend's uv source points at ../lib
RUN ln -s /opt/harness-lib /opt/lib

WORKDIR /opt/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend ./
RUN uv sync --frozen --no-dev

EXPOSE 8000
CMD ["/opt/backend/.venv/bin/uvicorn", "--factory", "backend.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
