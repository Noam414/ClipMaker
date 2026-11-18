FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV CLIPMAKER_HOST=0.0.0.0 \
    CLIPMAKER_PORT=8000 \
    CLIPMAKER_DATA_DIR=/data \
    CLIPMAKER_CACHE_DIR=/cache \
    DATABASE_URL=sqlite:////data/clipmaker.db

VOLUME ["/data", "/cache"]
EXPOSE 8000
CMD ["clipmaker", "serve"]
