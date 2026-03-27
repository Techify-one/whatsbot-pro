import base64
import json
import logging
import mimetypes
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class ContactMemory:
    """Persistent per-contact memory stored as a JSON file.

    File structure:
    {
        "phone": "5511999999999",
        "info": {"name": "", "email": "", "profession": "", "company": "", "observations": []},
        "messages": [{"role": "user"|"assistant", "content": "...", "ts": 1234567890}, ...],
        "created_at": 1234567890,
        "updated_at": 1234567890
    }
    """

    def __init__(self, phone: str, memory_dir: Path):
        self.phone = phone
        self.file_path = memory_dir / f"{phone}.json"
        self.id: int | None = None
        self.info: dict = {"name": "", "email": "", "profession": "", "company": "", "address": "", "observations": []}
        self.messages: list[dict] = []
        self.usage: list[dict] = []
        self.ai_enabled: bool = True
        self.is_group: bool = False
        self.group_name: str = ""
        self.unread_count: int = 0
        self.unread_ai_count: int = 0
        self.unread_msg_ids: list[str] = []
        self.created_at: float = time.time()
        self.updated_at: float = time.time()
        self._load()

    def _load(self):
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Migrate old "notes" format to structured "info"
                old_notes = data.get("notes", "")
                default_info = {"name": "", "email": "", "profession": "", "company": "", "address": "", "observations": []}
                self.info = data.get("info", default_info)
                if old_notes and not any(self.info.values()):
                    self.info["observations"] = [old_notes]
                self.messages = data.get("messages", [])
                self.usage = data.get("usage", [])
                self.ai_enabled = data.get("ai_enabled", True)
                self.is_group = data.get("is_group", False)
                self.group_name = data.get("group_name", "")
                self.id = data.get("id")
                self.unread_count = data.get("unread_count", 0)
                self.unread_ai_count = data.get("unread_ai_count", 0)
                self.unread_msg_ids = data.get("unread_msg_ids", [])
                self.created_at = data.get("created_at", time.time())
                self.updated_at = data.get("updated_at", time.time())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load memory for %s: %s", self.phone, e)

    def save(self):
        self.updated_at = time.time()
        data = {
            "id": self.id,
            "phone": self.phone,
            "info": self.info,
            "messages": self.messages,
            "usage": self.usage,
            "ai_enabled": self.ai_enabled,
            "is_group": self.is_group,
            "group_name": self.group_name,
            "unread_count": self.unread_count,
            "unread_ai_count": self.unread_ai_count,
            "unread_msg_ids": self.unread_msg_ids,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.error("Failed to save memory for %s: %s", self.phone, e)

    def add_message(self, role: str, content: str, *,
                    media_type: str | None = None, media_path: str | None = None,
                    status: str | None = None, msg_id: str | None = None):
        entry: dict = {"role": role, "content": content, "ts": time.time()}
        if media_type:
            entry["media_type"] = media_type
        if media_path:
            entry["media_path"] = media_path
        if status:
            entry["status"] = status
        if msg_id:
            entry["msg_id"] = msg_id
        self.messages.append(entry)
        self.save()

    def increment_unread(self, msg_id: str | None = None):
        self.unread_count += 1
        if msg_id:
            self.unread_msg_ids.append(msg_id)
        self.save()

    def increment_unread_ai(self):
        self.unread_ai_count += 1
        self.save()

    def mark_as_read(self) -> list[str]:
        """Reset unread count and return the list of unread msg_ids (for read receipts)."""
        msg_ids = list(self.unread_msg_ids)
        if self.unread_count > 0 or msg_ids or self.unread_ai_count > 0:
            self.unread_count = 0
            self.unread_ai_count = 0
            self.unread_msg_ids.clear()
            self.save()
        return msg_ids

    def set_ai_enabled(self, enabled: bool):
        self.ai_enabled = enabled
        self.save()

    def get_context_messages(self, limit: int) -> list[dict]:
        """Return the last N messages formatted for the LLM (without ts).

        For the most recent image message from the user, include a base64 data
        URI so the vision model can see it.  Older images are replaced with a
        placeholder to keep token usage reasonable.
        Transcription messages (role="transcription") are excluded from LLM context.
        """
        # Filter out transcription-only and failed messages before slicing
        eligible = [m for m in self.messages
                    if m.get("role") not in ("transcription", "tool_call", "system_notice") and m.get("status") != "failed"]
        recent = eligible[-limit:] if len(eligible) > limit else eligible

        # Find the index of the last user image message (within *recent*)
        last_image_idx = -1
        for i in range(len(recent) - 1, -1, -1):
            if recent[i].get("media_type") == "image" and recent[i]["role"] == "user":
                last_image_idx = i
                break

        result: list[dict] = []
        for i, m in enumerate(recent):
            mt = m.get("media_type")
            if mt == "image" and m["role"] == "user":
                if i == last_image_idx:
                    # Build vision content array with base64
                    content = _build_image_content(m.get("media_path", ""), m.get("content", ""))
                else:
                    content = m.get("content") or "[Imagem enviada pelo contato]"
                result.append({"role": m["role"], "content": content})
            else:
                result.append({"role": m["role"], "content": m.get("content", "")})
        return result

    def set_wa_name(self, wa_name: str) -> None:
        """Set contact name from WhatsApp pushName if no manual name exists.

        Auto-detected names are prefixed with '~' to distinguish from manual edits.
        If current name doesn't start with '~' and is non-empty, it's manual — don't overwrite.
        """
        current = self.info.get("name", "")
        if current and not current.startswith("~"):
            return
        new_name = f"~{wa_name}"
        if current != new_name:
            self.info["name"] = new_name
            self.save()

    def update_info(self, **kwargs):
        """Update contact info fields. Only overwrites non-empty values."""
        for key in ("name", "email", "profession", "company", "address"):
            val = kwargs.get(key, "")
            if val:
                self.info[key] = val
        observation = kwargs.get("observation", "")
        if observation and observation not in self.info.get("observations", []):
            self.info.setdefault("observations", []).append(observation)
        self.save()

    def add_usage(self, call_type: str, model: str,
                  prompt_tokens: int, completion_tokens: int,
                  total_tokens: int, cost_usd: float) -> None:
        self.usage.append({
            "call_type": call_type,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost_usd,
            "ts": time.time(),
        })
        self.save()

    def get_usage_summary(self, start_ts: float | None = None,
                          end_ts: float | None = None) -> dict:
        """Return aggregated usage stats for this contact."""
        filtered = self.usage
        if start_ts is not None:
            filtered = [u for u in filtered if u.get("ts", 0) >= start_ts]
        if end_ts is not None:
            filtered = [u for u in filtered if u.get("ts", 0) <= end_ts]

        totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                  "cost_usd": 0.0, "call_count": 0, "by_type": {}}
        for u in filtered:
            totals["prompt_tokens"] += u.get("prompt_tokens", 0)
            totals["completion_tokens"] += u.get("completion_tokens", 0)
            totals["total_tokens"] += u.get("total_tokens", 0)
            totals["cost_usd"] += u.get("cost_usd", 0.0)
            totals["call_count"] += 1
            ct = u.get("call_type", "text")
            bt = totals["by_type"].setdefault(ct, {
                "cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0,
                "total_tokens": 0, "call_count": 0,
            })
            bt["cost_usd"] += u.get("cost_usd", 0.0)
            bt["prompt_tokens"] += u.get("prompt_tokens", 0)
            bt["completion_tokens"] += u.get("completion_tokens", 0)
            bt["total_tokens"] += u.get("total_tokens", 0)
            bt["call_count"] += 1
        return totals

    def get_info_summary(self) -> str:
        """Format contact info for injection into system prompt."""
        parts = []
        if self.info.get("name"):
            parts.append(f"Nome: {self.info['name']}")
        if self.info.get("email"):
            parts.append(f"Email: {self.info['email']}")
        if self.info.get("profession"):
            parts.append(f"Profissão: {self.info['profession']}")
        if self.info.get("company"):
            parts.append(f"Empresa: {self.info['company']}")
        if self.info.get("address"):
            parts.append(f"Endereço: {self.info['address']}")
        for obs in self.info.get("observations", []):
            parts.append(f"Obs: {obs}")
        return "\n".join(parts)


def _build_image_content(media_path: str, caption: str = "") -> list[dict] | str:
    """Build an OpenAI vision content array from a local image file.

    Returns a plain placeholder string if the file cannot be read.
    """
    try:
        p = Path(media_path)
        if not p.is_absolute():
            # Resolve relative to project root
            p = Path(__file__).resolve().parent.parent / p
        if not p.exists():
            return caption or "[Imagem enviada pelo contato]"
        data = p.read_bytes()
        mime = mimetypes.guess_type(str(p))[0] or "image/png"
        b64 = base64.b64encode(data).decode()
        parts: list[dict] = [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]
        if caption:
            parts.append({"type": "text", "text": caption})
        else:
            parts.append({"type": "text", "text": "O contato enviou esta imagem."})
        return parts
    except Exception:
        return caption or "[Imagem enviada pelo contato]"
