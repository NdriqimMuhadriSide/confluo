# One image for both Python processes; the compose service picks the command
# (confluo-api or confluo-worker).
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY packages/core/pyproject.toml packages/core/
COPY modules/crm/pyproject.toml modules/crm/
COPY apps/api/pyproject.toml apps/api/
COPY apps/worker/pyproject.toml apps/worker/
RUN uv sync --frozen --no-dev --no-install-workspace
COPY packages packages
COPY modules modules
COPY apps/api apps/api
COPY apps/worker apps/worker
RUN uv sync --frozen --no-dev --all-packages --no-editable

FROM python:3.13-slim
RUN useradd --create-home --uid 10001 confluo
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER confluo
WORKDIR /app
CMD ["confluo-api"]
