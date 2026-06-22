FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libheif1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY image_helper ./image_helper

RUN pip install --no-cache-dir .

ENV HASH_DB_PATH=/data/hashes.sqlite3
VOLUME ["/data"]

ENTRYPOINT ["image-helper"]
