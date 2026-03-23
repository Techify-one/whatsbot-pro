import logging
import mimetypes
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Default device ID used across GOWA sessions (persisted per-instance)
_DEFAULT_DEVICE_NAME = "whatsbot"


class GOWAClient:
    """HTTP client for the GOWA REST API (go-whatsapp-web-multidevice v8.3.3)."""

    def __init__(self, port: int = 3000, timeout: float = 15.0):
        self.base_url = f"http://127.0.0.1:{port}"
        self.timeout = timeout
        self.device_id: str = _DEFAULT_DEVICE_NAME
        self._device_ready = False

    @property
    def _headers(self) -> dict:
        return {"X-Device-Id": self.device_id}

    def _request(self, method: str, path: str, raw: bool = False,
                 skip_device_header: bool = False, **kwargs) -> dict | bytes | None:
        url = f"{self.base_url}{path}"
        if skip_device_header:
            headers = kwargs.pop("headers", {})
        else:
            headers = {**self._headers, **kwargs.pop("headers", {})}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.request(method, url, headers=headers, **kwargs)
                resp.raise_for_status()
                ct = resp.headers.get("content-type", "")
                if raw or ct.startswith("image/"):
                    return resp.content
                return resp.json() if resp.text else {}
        except httpx.ConnectError:
            logger.debug("GOWA not reachable at %s", url)
            return None
        except httpx.HTTPStatusError as e:
            logger.error("GOWA HTTP %s %s -> %s", method, path, e.response.status_code)
            return None
        except Exception as e:
            logger.error("GOWA request error: %s", e)
            return None

    # ── Device Management ────────────────────────────────────────────

    def ensure_device(self) -> bool:
        """Ensure a device exists in GOWA, creating one if needed."""
        if self._device_ready:
            return True

        # Check if device already exists
        devices = self.list_devices()
        if devices:
            for d in devices:
                did = d.get("id", d.get("device", ""))
                if did == self.device_id:
                    self._device_ready = True
                    logger.info("GOWA device '%s' already exists.", self.device_id)
                    return True
            # Use the first existing device
            first = devices[0]
            self.device_id = first.get("id", first.get("device", self.device_id))
            self._device_ready = True
            logger.info("Using existing GOWA device '%s'.", self.device_id)
            return True

        # Create a new device
        result = self.create_device(self.device_id)
        if result:
            self._device_ready = True
            logger.info("Created GOWA device '%s'.", self.device_id)
            return True

        logger.error("Failed to create GOWA device.")
        return False

    def list_devices(self) -> list[dict]:
        """List all registered devices."""
        result = self._request("GET", "/devices", skip_device_header=True)
        if result and isinstance(result, dict):
            return result.get("results", None) or []
        return []

    def create_device(self, device_id: str | None = None) -> dict | None:
        """Create a new device in GOWA."""
        payload = {"device_id": device_id} if device_id else {}
        return self._request("POST", "/devices", json=payload, skip_device_header=True)

    # ── Health / Status ──────────────────────────────────────────────

    def health_check(self) -> bool:
        """Check if GOWA is running and reachable."""
        result = self._request("GET", "/devices", skip_device_header=True)
        return result is not None

    def get_status(self) -> dict | None:
        """Get WhatsApp connection status."""
        if not self._device_ready:
            self.ensure_device()
        return self._request("GET", "/app/status")

    def is_connected(self) -> bool:
        """Check if WhatsApp is connected."""
        status = self.get_status()
        if not status:
            return False
        results = status.get("results", status.get("data", status))
        if isinstance(results, dict):
            return results.get("is_logged_in", results.get("is_connected", False))
        return False

    # ── QR Code / Login ──────────────────────────────────────────────

    def get_qr_code(self) -> bytes | None:
        """Get QR code image for WhatsApp login.

        GOWA v8.3.3 returns JSON with a qr_link URL pointing to a PNG image.
        Returns None if already logged in or on error.
        """
        if not self._device_ready:
            self.ensure_device()
        # Skip if already connected
        if self.is_connected():
            logger.debug("get_qr_code: already connected, skipping.")
            return None
        result = self._request("GET", "/app/login")
        if not result or not isinstance(result, dict):
            logger.warning("get_qr_code: /app/login returned no data.")
            return None

        # Extract QR image URL from response
        results = result.get("results", result)
        qr_link = results.get("qr_link", "")
        if not qr_link:
            logger.warning("get_qr_code: no qr_link in response. keys=%s", list(results.keys()))
            return None

        # Download the QR image (use urlparse to extract path safely)
        qr_path = urlparse(qr_link).path
        logger.info("get_qr_code: fetching QR image from %s", qr_path)
        image_data = self._request("GET", qr_path, raw=True)
        if image_data and isinstance(image_data, bytes) and len(image_data) > 100:
            return image_data
        logger.warning("get_qr_code: QR image download failed or too small (%s bytes).",
                       len(image_data) if image_data else 0)
        return None

    # ── Messages ─────────────────────────────────────────────────────

    def _clean_phone(self, phone: str) -> str:
        return phone.strip().replace("+", "").replace(" ", "").replace("-", "")

    def send_message(self, phone: str, text: str) -> dict | None:
        """Send a text message to a phone number."""
        payload = {
            "phone": self._clean_phone(phone),
            "message": text,
        }
        return self._request("POST", "/send/message", json=payload)

    def send_image(self, phone: str, image_path: str, caption: str = "") -> dict | None:
        """Send an image to a phone number via multipart/form-data."""
        phone = self._clean_phone(phone)
        url = f"{self.base_url}/send/image"
        mime = mimetypes.guess_type(image_path)[0] or "image/png"
        try:
            with httpx.Client(timeout=30.0) as client:
                with open(image_path, "rb") as f:
                    files = {"image": (Path(image_path).name, f, mime)}
                    data = {"phone": phone}
                    if caption:
                        data["caption"] = caption
                    resp = client.post(url, headers=self._headers, data=data, files=files)
                    resp.raise_for_status()
                    return resp.json() if resp.text else {}
        except Exception as e:
            logger.error("GOWA send_image error: %s", e)
            return None

    def send_audio(self, phone: str, audio_path: str) -> dict | None:
        """Send an audio file to a phone number via multipart/form-data."""
        phone = self._clean_phone(phone)
        url = f"{self.base_url}/send/audio"
        mime = mimetypes.guess_type(audio_path)[0] or "audio/ogg"
        try:
            with httpx.Client(timeout=30.0) as client:
                with open(audio_path, "rb") as f:
                    files = {"audio": (Path(audio_path).name, f, mime)}
                    data = {"phone": phone}
                    resp = client.post(url, headers=self._headers, data=data, files=files)
                    resp.raise_for_status()
                    return resp.json() if resp.text else {}
        except Exception as e:
            logger.error("GOWA send_audio error: %s", e)
            return None

    def get_chats(self, limit: int = 20) -> list[dict]:
        """Get list of chats."""
        result = self._request("GET", f"/chats?limit={limit}")
        if result and isinstance(result, dict):
            # v8.3.3 nests list under results.data
            results = result.get("results", {})
            if isinstance(results, dict):
                return results.get("data", []) or []
            if isinstance(results, list):
                return results
        return []

    def get_chat_messages(self, chat_jid: str, limit: int = 20) -> list[dict]:
        """Get messages from a specific chat."""
        result = self._request("GET", f"/chat/{chat_jid}/messages?limit={limit}")
        if result and isinstance(result, dict):
            results = result.get("results", {})
            if isinstance(results, dict):
                return results.get("data", []) or []
            if isinstance(results, list):
                return results
        return []

    # ── Session ──────────────────────────────────────────────────────

    def logout(self) -> dict | None:
        """Disconnect from WhatsApp."""
        return self._request("GET", "/app/logout")

    def reconnect(self) -> dict | None:
        """Reconnect to WhatsApp."""
        return self._request("GET", "/app/reconnect")
