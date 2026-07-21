FROM python:3.11-slim

ARG GOWA_VERSION=8.11.0
ARG TARGETARCH=amd64

ENV WHATSBOT_DOCKER=1
ENV PYTHONUNBUFFERED=1
# Pin the web port to match EXPOSE/HEALTHCHECK below. Coolify deploys from this
# Dockerfile (not docker-compose.yaml), so without this the app falls back to the
# ConfigKey default (8090) while the healthcheck probes 8080 → "unhealthy".
ENV WHATSBOT_WEB_PORT=8080

# Install curl and unzip for downloading GOWA; ffmpeg (+ffprobe) for video
# validation/transcode to the WhatsApp Cloud limits (plano 65 F5B).
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl unzip ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Download and install GOWA binary for Linux
RUN curl -fsSL "https://github.com/aldinokemal/go-whatsapp-web-multidevice/releases/download/v${GOWA_VERSION}/whatsapp_${GOWA_VERSION}_linux_${TARGETARCH}.zip" \
        -o /tmp/gowa.zip && \
    unzip /tmp/gowa.zip -d /tmp/gowa && \
    cp /tmp/gowa/linux-${TARGETARCH} /usr/local/bin/gowa && \
    chmod +x /usr/local/bin/gowa && \
    rm -rf /tmp/gowa /tmp/gowa.zip

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code only
COPY agent/ agent/
COPY ai_engine/ ai_engine/
COPY app/ app/
COPY assets/ assets/
COPY channels/ channels/
COPY config/ config/
COPY domain/ domain/
COPY gowa/ gowa/
COPY db/ db/
COPY plugins/ plugins/
COPY runtime/ runtime/
COPY server/ server/
COPY web/ web/
COPY main.py alembic.ini ./

# Create bin/gowa symlink so gowa/manager.py finds the binary at expected path
RUN mkdir -p bin && ln -s /usr/local/bin/gowa bin/gowa

# Create runtime directories. NOTE: persistence is NOT declared here on purpose.
# A Dockerfile `VOLUME` creates an ANONYMOUS volume that Coolify (and `docker run`
# without -v) DISCARDS when the container is recreated on redeploy — uploaded
# media under statics/senditems/ and the SQLite DB would silently vanish.
# Persist these by binding real host folders instead:
#   - docker compose: ./data/{storages,statics,logs} (see docker-compose.yaml)
#   - Coolify: map /app/storages and /app/statics to Persistent Storage
RUN mkdir -p logs storages statics

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["python", "main.py"]
