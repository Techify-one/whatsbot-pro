#!/bin/bash
# WhatsBot — Linux/Docker launcher
set -e

# Create persistent data directories
mkdir -p data/contacts data/storages data/statics data/logs

# Create empty config.json if not present (required for Docker volume mount)
if [ ! -f data/config.json ]; then
    echo '{}' > data/config.json
fi

# Build and start
docker compose up --build -d

echo ""
echo "WhatsBot iniciado!"
echo "Web UI: http://localhost:${WHATSBOT_WEB_PORT:-8080}"
echo "Logs:   docker compose logs -f"
