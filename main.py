"""WhatsBot — WhatsApp AI Bot with Web GUI."""

import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path

from config.settings import get_data_dir


def main():
    data_dir = get_data_dir()

    # Initialize database before anything else.
    # URL resolution (Postgres-only, plano 29): exclusivamente a env DATABASE_URL.
    from db import init_db
    is_docker = os.environ.get("WHATSBOT_DOCKER") == "1" or Path("/.dockerenv").exists()
    storages_dir = data_dir / "storages"
    storages_dir.mkdir(exist_ok=True)
    init_db(storages_dir=storages_dir)

    # Now load settings (reads from the database)
    from config.settings import Settings
    settings = Settings()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                settings.logs_dir / "whatsbot.log",
                encoding="utf-8",
            ),
        ],
    )
    logger = logging.getLogger("whatsbot")
    logger.info("WhatsBot starting...")

    from gowa.manager import GOWAManager
    from gowa.client import GOWAClient
    from agent.handler import AgentHandler
    from server.app import create_app

    port = settings.get("gowa_port", 3000)
    web_port = settings.get("web_port", 8080)

    # GOWA ingresses through the generic per-channel route (plano 13 Fase 0):
    # POST → registry.get("default").parse_inbound → _dispatch_events → ingest_event,
    # the SAME funnel as Cloud/Telegram. The legacy exact /api/webhook fallback was
    # retired in plano 23 Fase F2 — this generic path is the only GOWA ingress.
    webhook_url = f"http://127.0.0.1:{web_port}/api/webhook/gowa/default"
    gowa_manager = GOWAManager(port=port, data_dir=settings.data_dir, webhook_url=webhook_url)
    gowa_client = GOWAClient(port=port)

    agent_handler = AgentHandler(
        api_key=settings.get("openrouter_api_key", ""),
        max_context_messages=settings.get("max_context_messages", 10),
        inactivity_timeout_min=settings.get("inactivity_timeout_min", 30),
        audio_model=settings.get("audio_model", "google/gemini-3-flash-preview"),
        image_model=settings.get("image_model", "google/gemini-3-flash-preview"),
        document_model=settings.get("document_model", "google/gemini-2.5-flash"),
        improvement_model=settings.get("improvement_model", ""),
        default_ai_enabled=settings.get("default_ai_enabled", True),
    )

    app = create_app(
        settings=settings,
        gowa_manager=gowa_manager,
        gowa_client=gowa_client,
        agent_handler=agent_handler,
    )

    host = "0.0.0.0"

    # Open browser after server has time to start (skip in Docker — no display)
    if not is_docker:
        url = f"http://127.0.0.1:{web_port}"
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    import uvicorn
    logger.info("Starting web server on http://%s:%d", host, web_port)
    uvicorn.run(app, host=host, port=web_port, log_level="warning")
    logger.info("WhatsBot exiting.")


if __name__ == "__main__":
    main()
