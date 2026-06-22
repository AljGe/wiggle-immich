FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libheif1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
COPY image_helper ./image_helper

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENV HASH_DB_PATH=/data/hashes.sqlite3
VOLUME ["/data"]

ENTRYPOINT ["image-helper"]
