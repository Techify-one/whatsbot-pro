import json
import os
from pathlib import Path


def get_data_dir() -> Path:
    """Return the application data directory (project root)."""
    data_dir = Path(__file__).resolve().parent.parent
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


DEFAULT_CONFIG = {
    "openrouter_api_key": "",
    "model": "openai/gpt-4o-mini",
    "audio_model": "google/gemini-2.0-flash-001",
    "image_model": "google/gemini-2.0-flash-001",
    "system_prompt": (
        "Você é um assistente útil e amigável. Responda de forma clara e concisa. "
        "Use português brasileiro."
    ),
    "auto_reply": True,
    "reply_to_all": True,
    "only_saved_contacts": False,
    "max_context_messages": 10,
    "inactivity_timeout_min": 30,
    "message_batch_delay": 3.0,
    "response_delay_min": 1.0,
    "response_delay_max": 3.0,
    "gowa_port": 3000,
    "web_port": 8080,
    "usd_brl_rate": 5.50,
    "split_messages": True,
    "split_message_delay": 2.0,
    "group_reply_mode": "mention_only",
    "bot_phone": "",
    "bot_name": "",
}


class Settings:
    def __init__(self):
        self.data_dir = get_data_dir()
        self.config_path = self.data_dir / "config.json"
        self.logs_dir = self.data_dir / "logs"
        self.logs_dir.mkdir(exist_ok=True)
        self._config: dict = {}
        self.load()

    def load(self):
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._config = {}
        # Merge defaults for missing keys
        added = False
        for key, value in DEFAULT_CONFIG.items():
            if key not in self._config:
                self._config[key] = value
                added = True
        # Persist on first run or when new defaults were added
        if added or not self.config_path.exists():
            self.save()

    def save(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self._config, f, indent=2, ensure_ascii=False)

    def get(self, key: str, default=None):
        return self._config.get(key, default)

    def set(self, key: str, value):
        self._config[key] = value

    def __getitem__(self, key):
        return self._config[key]

    def __setitem__(self, key, value):
        self._config[key] = value
