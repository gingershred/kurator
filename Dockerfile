# syntax=docker/dockerfile:1
# kurator image: code only. All state lives in the /data volume.

ARG PYTHON_VERSION=3.12

# --- build: install dependencies into a virtualenv with uv --------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# yt-dlp must keep up with YouTube: every build (including the weekly uncached rebuild)
# installs the newest stable release instead of the version pinned in uv.lock.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv/bin/python --upgrade \
        --refresh-package yt-dlp --refresh-package yt-dlp-ejs "yt-dlp[default]"

# --- Deno: JavaScript runtime yt-dlp needs for YouTube ---------------------------------------
FROM denoland/deno:bin AS deno

# --- runtime ------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

ARG KURATOR_GIT_SHA=""
LABEL org.opencontainers.image.title="kurator" \
      org.opencontainers.image.description="Self-hosted, filtered frontend for YouTube subscriptions. No Shorts." \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.source="https://github.com/gingershred/kurator"

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    KURATOR_DATA_DIR=/data \
    KURATOR_GIT_SHA=${KURATOR_GIT_SHA}

# ffmpeg merges audio/video and converts subtitles for downloads.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*
COPY --from=deno /deno /usr/local/bin/deno

RUN groupadd --gid 1000 kurator \
 && useradd --uid 1000 --gid 1000 --home-dir /app --no-create-home \
            --shell /usr/sbin/nologin kurator \
 && mkdir /data && chown kurator:kurator /data

COPY --from=builder /app /app
WORKDIR /app
USER kurator
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-m", "app.cli", "healthcheck"]

CMD ["python", "-m", "app.cli", "serve"]
