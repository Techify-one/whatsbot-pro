"""Dev-mode entry point for uvicorn --reload.

uvicorn imports this as 'server.dev:app' and re-imports on every file change,
recreating the app with fresh settings.
"""

import logging
import sys

# Configure logging BEFORE importing server.app (which adds MemoryLogHandler)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

# Silence noisy framework loggers in dev console
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
logging.getLogger("watchfiles.main").setLevel(logging.WARNING)

from config.settings import Settings
from gowa.manager import GOWAManager
from gowa.client import GOWAClient
from agent.handler import AgentHandler
from usage.tracker import UsageTracker
from server.app import create_app

settings = Settings()
port = settings.get("gowa_port", 3000)
web_port = settings.get("web_port", 8080)

webhook_url = f"http://127.0.0.1:{web_port}/api/webhook"
usage_tracker = UsageTracker(settings.data_dir)
app = create_app(
    settings=settings,
    gowa_manager=GOWAManager(port=port, data_dir=settings.data_dir, webhook_url=webhook_url),
    gowa_client=GOWAClient(port=port),
    agent_handler=AgentHandler(
        api_key=settings.get("openrouter_api_key", ""),
        system_prompt=settings.get("system_prompt", "Você é um assistente útil."),
        max_context_messages=settings.get("max_context_messages", 10),
        inactivity_timeout_min=settings.get("inactivity_timeout_min", 30),
        model=settings.get("model", "openai/gpt-4o-mini"),
        audio_model=settings.get("audio_model", "google/gemini-2.0-flash-001"),
        image_model=settings.get("image_model", "google/gemini-2.0-flash-001"),
        memory_dir=settings.data_dir / "contacts",
        usage_tracker=usage_tracker,
    ),
    usage_tracker=usage_tracker,
)
