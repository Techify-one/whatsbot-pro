"""WhatsBot — WhatsApp AI Bot with Web GUI."""

import logging
import sys
import threading
import webbrowser
from pathlib import Path

# Setup logging early
from config.settings import Settings

settings = Settings()

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


def main():
    logger.info("WhatsBot starting...")

    from gowa.manager import GOWAManager
    from gowa.client import GOWAClient
    from agent.handler import AgentHandler
    from usage.tracker import UsageTracker
    from server.app import create_app

    port = settings.get("gowa_port", 3000)
    web_port = settings.get("web_port", 8080)

    webhook_url = f"http://127.0.0.1:{web_port}/api/webhook"
    gowa_manager = GOWAManager(port=port, data_dir=settings.data_dir, webhook_url=webhook_url)
    gowa_client = GOWAClient(port=port)
    usage_tracker = UsageTracker(settings.data_dir)

    agent_handler = AgentHandler(
        api_key=settings.get("openrouter_api_key", ""),
        system_prompt=settings.get("system_prompt", "Você é um assistente útil."),
        max_context_messages=settings.get("max_context_messages", 10),
        inactivity_timeout_min=settings.get("inactivity_timeout_min", 30),
        model=settings.get("model", "openai/gpt-4o-mini"),
        audio_model=settings.get("audio_model", "google/gemini-2.0-flash-001"),
        image_model=settings.get("image_model", "google/gemini-2.0-flash-001"),
        memory_dir=settings.data_dir / "contacts",
        usage_tracker=usage_tracker,
    )

    app = create_app(
        settings=settings,
        gowa_manager=gowa_manager,
        gowa_client=gowa_client,
        agent_handler=agent_handler,
        usage_tracker=usage_tracker,
    )

    # Open browser after server has time to start
    url = f"http://127.0.0.1:{web_port}"
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    import uvicorn
    logger.info("Starting web server on http://127.0.0.1:%d", web_port)
    uvicorn.run(app, host="127.0.0.1", port=web_port, log_level="warning")
    logger.info("WhatsBot exiting.")


if __name__ == "__main__":
    main()
