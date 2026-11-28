FROM python:3.13-slim

# WITH_YOUTUBE=1 (the Oracle deploy kit sets it, D-028) adds what YouTube links need on a
# server: Node.js for yt-dlp's player challenges (yt-dlp-ejs) and the client plugin of the
# PO-token provider, which runs as its own container (bgutil, GPL-3.0, loaded only into the
# separate yt-dlp process, D-019).
ARG WITH_YOUTUBE=0

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && if [ "$WITH_YOUTUBE" = "1" ]; then apt-get install -y --no-install-recommends nodejs; fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && if [ "$WITH_YOUTUBE" = "1" ]; then \
         pip install --no-cache-dir "yt-dlp-ejs==0.8.0" "bgutil-ytdlp-pot-provider==2.0.2"; fi

ENV CLIPMAKER_HOST=0.0.0.0 \
    CLIPMAKER_PORT=8000 \
    CLIPMAKER_DATA_DIR=/data \
    CLIPMAKER_CACHE_DIR=/cache \
    DATABASE_URL=sqlite:////data/clipmaker.db

VOLUME ["/data", "/cache"]
EXPOSE 8000
CMD ["clipmaker", "serve"]
