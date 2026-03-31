#!/bin/bash
# WhatsBot — Linux/Docker launcher
set -e

# Create persistent data directories
mkdir -p data/storages data/statics data/logs

# Build and start
docker compose up --build -d

echo ""
echo "WhatsBot iniciado!"
echo "Web UI: http://localhost:${WHATSBOT_WEB_PORT:-8080}"
echo "Logs:   docker compose logs -f"
