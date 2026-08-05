"""Internal GOWA channel adapter (plano 02 Fase 0).

Wraps the existing ``GOWAClient`` / ``GOWAManager`` behind the ``Channel``
contract so the registry can be exercised with a real provider before GOWA is
extracted to a plugin (Fase 3, where this moves to
``storages/plugins/gowa/channels.py``).

This phase keeps the live webhook + send flow untouched; ``parse_inbound`` is a
thin placeholder until the webhook parsing is extracted into a pure function
(plano 02 §0.5). Outbound delegation is wired and ready but not yet the only path.
"""

from __future__ import annotations

import logging

from channels.base import AccountIdentity, Channel, ChannelCapabilities, SendResult
from channels.br_phone import br_phone_variants
from channels.events import InboundEvent
from gowa.client import GOWASendError, extract_msg_id

logger = logging.getLogger(__name__)


def _canonical_phone(digits: str) -> str:
    """One deterministic canonical form for a BR number's 12↔13-digit variants.

    ``br_phone_variants`` yields the same *set* of forms regardless of which one
    we start from; picking the shortest (then lexicographically) collapses both
    the 12- and 13-digit representations of the SAME line to one dedup key. Non-BR
    numbers have a single variant and pass through unchanged.
    """
    variants = br_phone_variants(digits)
    return min(variants, key=lambda v: (len(v), v))


# Which WhatsApp chat types (by JID suffix) a GOWA channel surfaces as
# conversations. The provider OWNS this catalogue (plano 33): the core form only
# renders it as a generic multiselect from the descriptor — it never knows what a
# "JID type" is. Keys mirror ``channels/jid.py``; the user only sees the label.
GOWA_JID_TYPES = [
    {"value": "person", "label": "Pessoa (contato)",
     "hint": "Conversas individuais com contatos."},
    {"value": "person_lid", "label": "Pessoa (modo privacidade)",
     "hint": "Contatos que ocultam o número (modo privacidade)."},
    {"value": "group", "label": "Grupo / Comunidade",
     "hint": "Mensagens de grupos e comunidades."},
    {"value": "newsletter", "label": "Canal",
     "hint": "Publicações de Canais do WhatsApp."},
    {"value": "broadcast", "label": "Status / Transmissão",
     "hint": "Status e listas de transmissão."},
    {"value": "bot", "label": "Bot (Meta AI etc.)",
     "hint": "Conversas com bots como o Meta AI."},
]
# CREATE-time default: seeds the form of a NEW channel (descriptor →
# ``initialConfigValues``). ``group`` starts UNCHECKED — a number created for
# one-to-one support was materializing every group it belongs to as a
# conversation (118 phantom contacts, plano 102/103). The option stays visible
# and one click away.
# ⚠️ NOT ``channels.jid.DEFAULT_ALLOWED_JID_TYPES``, the RUNTIME fallback for a
# channel with no saved key: changing THAT one is retroactive (it would silence
# groups on legacy channels).
GOWA_DEFAULT_JID_TYPES = ["person", "person_lid"]


class GOWAChannel(Channel):
    provider = "gowa"

    def __init__(self, channel_id: str, gowa_client=None, gowa_manager=None):
        super().__init__(channel_id, ChannelCapabilities(
            qr=True, templates=False, groups=True, presence=True,
            reactions=True, media=True, revoke=True, edit_message=True,
            inbound_route="path",
        ))
        self._client = gowa_client
        self._manager = gowa_manager

    # ── Contact type (marca por canal — plano tipos-de-contato) ──────
    @classmethod
    def contact_type(cls) -> str:
        """GOWA é WhatsApp — mesmo tipo que o WhatsApp Cloud."""
        return "whatsapp"

    # ── source_id form (dedup key — plano 42) ────────────────────────
    @classmethod
    def source_id_for(cls, phone: str, is_group: bool) -> str:
        """WhatsApp JID form: ``<phone>@g.us`` for groups, ``<phone>@s.whatsapp.net``
        otherwise — byte-identical to the pre-plano-42 ``ContactMemory._jid`` and the
        migration 0013 backfill, so existing ``contact_inboxes.source_id`` rows keep
        resolving. ``is_group`` (from the contact row) selects group vs person."""
        suffix = "g.us" if is_group else "s.whatsapp.net"
        return f"{phone}@{suffix}"

    # ── Provider descriptor (plano 33) ───────────────────────────────
    @classmethod
    def provider_descriptor(cls) -> dict:
        """GOWA is QR/linked-device: no credentials, a generated device id, and a
        JID-type multiselect deciding which chat types surface as conversations.
        ``needs_qr`` drives the post-create QR panel (no ``post_create`` block)."""
        return {
            "provider": "gowa",
            "label": "GOWA",
            "color": "green",
            # Plano 52: outbound proxy per NUMBER. A non-empty proxy moves this
            # channel to a DEDICATED GOWA process with WHATSAPP_PROXY set (the
            # orchestration lives in the gowa plugin's processes.py). ``secret``
            # type => password input + masked at the API edge + edit-placeholder
            # semantics, all generic (channel_credentials).
            "credential_fields": [{
                "key": "proxy_url",
                "label": "Proxy de saída (opcional)",
                "type": "secret",
                "required": False,
                "placeholder": "socks5://usuario:senha@ip:porta",
                "help": "IP FIXO e dedicado deste número (socks5:// ou http(s)://). "
                        "Use 1 IP exclusivo por número — nunca proxy rotativo. "
                        "Salvar/alterar o proxy reinicia a sessão deste número — "
                        "será preciso ler o QR de novo.",
            }],
            "config_fields": [
                {"key": "gowa_device_id", "label": "GOWA Device ID",
                 "type": "generated", "prefix": "gowa_",
                 "help": "Identifica este número dentro do GOWA. Após criar, "
                         "leia o QR Code para conectar o WhatsApp."},
                {"key": "allowed_jid_types",
                 "label": "O que deve aparecer no painel",
                 "type": "multiselect", "options": GOWA_JID_TYPES,
                 "default": list(GOWA_DEFAULT_JID_TYPES),
                 "help": "Escolha quais tipos de conversa deste número viram "
                         "conversa. Os tipos desmarcados são ignorados (não "
                         "aparecem no painel). Atenção: marcar \"Grupo / "
                         "Comunidade\" faz TODO grupo de que este número "
                         "participa virar conversa no painel."},
                {"key": "disconnect_alert_enabled",
                 "label": "Avisar no Telegram se este número cair",
                 "type": "bool", "default": True,
                 "help": "Quando ligado, quedas de conexão deste canal entram "
                         "no alerta agregado do Telegram. O bot/chat/fuso ficam "
                         "na configuração do plugin GOWA."},
            ],
            "capabilities": {"needs_qr": True, "templates": False},
            "ai_sequential_default": True,
            "post_create": None,
            "form_component": None,
        }

    # ── Lifecycle ────────────────────────────────────────────────────
    def start(self) -> None:
        if self._manager is not None:
            self._manager.start()
        # Register this channel's device in the (shared) GOWA process so it can
        # hold its own WhatsApp session — multi-device on a single GOWA process.
        if self._client is not None:
            try:
                self._client.ensure_device()
            except Exception as e:  # noqa: BLE001
                logger.debug("GOWAChannel.start: ensure_device failed: %s", e)

    def stop(self) -> None:
        if self._manager is not None:
            self._manager.stop()

    def status(self) -> dict:
        """Live status of this channel's **device** (plano 27 D1).

        ``connected``/``logged_in`` come straight from the GOWA ``/app/status``
        of *this* device via ``connection_state()`` — no longer derived from the
        subprocess (``manager.is_running``), which since plano 13 is spawned by
        the plugin and never sets ``gowa_manager._managed`` (so it read False for
        every channel, showing "Desconectado" even for a working number).
        """
        try:
            st = self._client.connection_state() if self._client else {}
        except Exception as e:  # noqa: BLE001
            return {"connected": False, "logged_in": False, "needs_qr": False,
                    "own_phone": "", "error": str(e)}
        connected = bool(st.get("connected"))
        logged_in = bool(st.get("logged_in"))
        own_phone = ""
        if logged_in:
            try:
                own_phone = self._client.get_own_number() or ""
            except Exception:  # noqa: BLE001
                pass
        return {"connected": connected, "logged_in": logged_in,
                "needs_qr": not logged_in,
                "own_phone": own_phone, "error": None}

    # ── Account identity (dedup contract — plano 32 F5) ──────────────
    def account_identity(self) -> AccountIdentity | None:
        """This device's own WhatsApp number (canonical BR), or ``None``.

        Only knowable AFTER the QR pairing, so this is the live hook (the status
        sweep calls it). Uses the device-scoped ``get_own_number`` (plano 32 F1),
        so it never returns another channel's number. ``kind="phone"``. ``None``
        while not logged in / unresolved. The base ``reject_duplicate`` (logout)
        severs a device that paired to an already-connected number.
        """
        if self._client is None:
            return None
        try:
            raw = self._client.get_own_number() or ""
        except Exception:  # noqa: BLE001
            return None
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            return None
        return AccountIdentity("phone", _canonical_phone(digits))

    def get_qr(self) -> bytes | None:
        """PNG bytes of this device's login QR (None if logged in / unavailable).

        Re-verifies the GOWA device exists before requesting a QR. A device that
        was never paired can vanish from GOWA (dropped on a GOWA restart, or
        removed by a prior logout), while this client still has it cached as
        ready — which makes ``/app/login`` fail with DEVICE_NOT_FOUND. Clearing
        the cache forces ``ensure_device`` to recreate it, so connecting an
        existing channel behaves like connecting a freshly-created one.

        This is the canonical name (matches ``Channel.get_qr``). The legacy
        :meth:`qr` is a deprecated alias kept one wave for compatibility
        (plano 23 Fase C4 — Expand-Contract).
        """
        if self._client is None:
            return None
        try:
            self._client._device_ready = False
            self._client.ensure_device()
        except Exception as e:  # noqa: BLE001
            logger.debug("GOWAChannel.get_qr: ensure_device failed: %s", e)

        # Decide QR vs reconnect vs logout+relogin from the real device state
        # (plano 27 F2.2) instead of blindly emitting a QR.
        try:
            st = self._client.connection_state()
        except Exception as e:  # noqa: BLE001
            logger.debug("GOWAChannel.get_qr: connection_state failed: %s", e)
            st = {}
        if st.get("connected"):
            # Socket already alive — nothing to do; the poll will close the QR UI.
            return None
        if st.get("logged_in"):
            # Session exists but the socket dropped — reconnect, don't show a QR.
            try:
                self._client.reconnect()
            except Exception as e:  # noqa: BLE001
                logger.debug("GOWAChannel.get_qr: reconnect failed: %s", e)
            return None

        # Not logged in → normal QR flow.
        png = self._client.get_qr_code()
        if png:
            return png
        # Stale/stuck session: device unpaired yet /app/login refuses a QR. As a
        # last resort clear the credential (logout) and retry once. Only reached
        # when we already know the device is neither connected nor logged in, so
        # logout can't nuke a live session. Defensive — never breaks the endpoint.
        try:
            self._client.logout()
            return self._client.get_qr_code()
        except Exception as e:  # noqa: BLE001
            logger.debug("GOWAChannel.get_qr: logout+relogin failed: %s", e)
            return None

    # Deprecated alias (plano 23 Fase C4). ``get_qr`` is canonical; ``qr`` stays
    # for one wave so any caller still on the old name (and any third-party
    # provider mirroring it) keeps working. Logs a one-time deprecation note.
    _qr_deprecation_warned = False

    def qr(self) -> bytes | None:
        """Deprecated alias for :meth:`get_qr`. Use ``get_qr`` instead."""
        if not GOWAChannel._qr_deprecation_warned:
            GOWAChannel._qr_deprecation_warned = True
            logger.warning(
                "GOWAChannel.qr() is deprecated; use get_qr() — the alias will "
                "be removed in a future release.")
        return self.get_qr()

    # ── Session (per-channel reconnect / logout) ─────────────────────
    def reconnect(self) -> dict:
        """Reconnect this channel's device socket (plano 27). Acts on the right
        device via this channel's own client — not the singleton."""
        if self._client is None:
            return {"ok": False, "error": "cliente GOWA indisponível"}
        try:
            self._client.reconnect()
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def logout(self) -> dict:
        """Log this channel's device out of WhatsApp (plano 27). Clears the
        session so the next connect asks for a fresh QR."""
        if self._client is None:
            return {"ok": False, "error": "cliente GOWA indisponível"}
        try:
            self._client.logout()
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    # ── Outbound (delegates to the existing client) ──────────────────
    def send_text(self, chat_id: str, text: str, *, reply_to=None,
                  mentions=None) -> SendResult:
        try:
            res = self._client.send_message(chat_id, text, mentions=mentions,
                                            reply_message_id=reply_to)
            return SendResult(ok=True, external_msg_id=extract_msg_id(res) or "")
        except Exception as e:  # noqa: BLE001
            return SendResult(ok=False, error=str(e))

    def send_media(self, chat_id: str, kind: str, path_or_url: str, *,
                   caption: str = "", filename=None) -> SendResult:
        try:
            if kind == "image":
                res = self._client.send_image(chat_id, path_or_url, caption=caption)
            elif kind == "audio":
                res = self._client.send_audio(chat_id, path_or_url)
            elif kind == "video":
                # /send/video existe no binário atual; um GOWA mais antigo
                # responderia 404 — nesse caso degrada para documento (entregue,
                # só que como anexo) em vez de falhar o envio.
                try:
                    res = self._client.send_video(chat_id, path_or_url, caption=caption)
                except GOWASendError as e:
                    if getattr(e, "error_type", "") != "api":
                        raise
                    logger.warning("gowa /send/video indisponível (%s) — enviando como documento", e)
                    res = self._client.send_file(chat_id, path_or_url, caption=caption,
                                                 filename=filename)
            else:
                res = self._client.send_file(chat_id, path_or_url, caption=caption,
                                             filename=filename)
            return SendResult(ok=True, external_msg_id=extract_msg_id(res) or "")
        except Exception as e:  # noqa: BLE001
            return SendResult(ok=False, error=str(e))

    # ── Optional capabilities (delegate to the existing client) ──────
    def mark_read(self, chat_id: str, msg_id: str) -> None:
        try:
            self._client.mark_as_read(msg_id, chat_id)
        except Exception:  # noqa: BLE001
            logger.debug("gowa mark_read failed", exc_info=True)

    def send_presence(self, chat_id: str, state: str) -> None:
        try:
            if state in ("paused", "stop", "available"):
                self._client.stop_chat_presence(chat_id)
            else:  # composing / recording / start
                self._client.send_chat_presence(chat_id)
        except Exception:  # noqa: BLE001
            logger.debug("gowa send_presence failed", exc_info=True)

    def react(self, chat_id: str, msg_id: str, emoji: str) -> None:
        try:
            self._client.react_to_message(msg_id, chat_id, emoji)
        except Exception:  # noqa: BLE001
            logger.debug("gowa react failed", exc_info=True)

    def revoke(self, chat_id: str, msg_id: str) -> None:
        try:
            self._client.revoke_message(msg_id, chat_id)
        except Exception:  # noqa: BLE001
            logger.debug("gowa revoke failed", exc_info=True)

    def edit_text(self, chat_id: str, msg_id: str, text: str) -> SendResult:
        """Edit a sent message via GOWA ``POST /message/{id}/update``."""
        try:
            self._client.update_message(msg_id, chat_id, text)
            return SendResult(ok=True, external_msg_id=msg_id)
        except Exception as e:  # noqa: BLE001
            return SendResult(ok=False, error=str(e))

    def fetch_avatar(self, chat_id: str) -> bytes | None:
        """Profile photo bytes via this device's GOWA ``/user/avatar`` (plano 38 F5)."""
        if self._client is None:
            return None
        try:
            data = self._client.get_avatar(chat_id)
        except Exception:  # noqa: BLE001
            logger.debug("gowa fetch_avatar failed", exc_info=True)
            return None
        return data if isinstance(data, bytes) else None

    def check_phone(self, phone: str) -> dict:
        """Verify the number on WhatsApp via GOWA ``/user/check`` (resolves the BR
        canonical phone too). Propagates ``GOWASendError`` so the caller can surface
        a real connection problem (e.g. HTTP 401 when this device isn't logged in)."""
        if self._client is None:
            return {"registered": True, "canonical_phone": phone, "name": ""}
        return self._client.check_phone(phone)

    # ── Inbound ──────────────────────────────────────────────────────
    def parse_inbound(self, raw: dict) -> list[InboundEvent]:
        """Translate a raw GOWA webhook body into InboundEvents (plano 13 Fase 0).

        Delegates to ``gowa.inbound.parse_gowa_inbound`` (the extracted pure-ish
        parser), passing this channel's client + the configured group_reply_mode.
        Bot identity defaults to what ``group_mentions`` holds (set by the status
        poll). Blocking client/DB lookups happen inside — callers run it via
        ``asyncio.to_thread``.
        """
        from gowa.inbound import parse_gowa_inbound
        group_mode = "mention_only"
        try:
            from db.repositories import config_repo
            group_mode = config_repo.get("group_reply_mode", "mention_only") or "mention_only"
        except Exception:  # noqa: BLE001
            pass
        return parse_gowa_inbound(raw, channel_id=self.channel_id,
                                  client=self._client, group_mode=group_mode)


def build_gowa_channel(channel_id: str, row: dict | None, *,
                       gowa_client, gowa_manager) -> "GOWAChannel":
    """Build a live ``GOWAChannel`` for a channel row.

    The ``default`` channel reuses the singleton client (device ``whatsbot``)
    that drives the legacy message pipeline. Every other GOWA channel gets its
    own ``GOWAClient`` bound to its ``gowa_device_id`` but pointing at the same
    (shared) GOWA process — i.e. N WhatsApp numbers as N devices on one GOWA.
    """
    if channel_id == "default":
        return GOWAChannel(channel_id, gowa_client, gowa_manager)
    device_id = ((row or {}).get("gowa_device_id") or channel_id)
    # Import here to avoid a circular import at module load time.
    from gowa.client import GOWAClient
    # Plano 52: a channel with a DEDICATED (proxied) process keeps its allocated
    # port in config["gowa_dedicated_port"] (persisted by the gowa plugin's
    # reconcile). Absent/invalid ⇒ the shared process port — byte-identical to
    # the pre-plano-52 behaviour.
    port = getattr(gowa_client, "port", 3000)
    try:
        import json as _json
        cfg = _json.loads((row or {}).get("config") or "{}") or {}
        dedicated = int(cfg.get("gowa_dedicated_port") or 0)
        if dedicated > 0:
            port = dedicated
    except Exception:  # noqa: BLE001 — malformed config must never block the build
        pass
    client = GOWAClient(port=port)
    client.device_id = device_id
    client.strict_device = True  # bind to its own device, never adopt another's
    return GOWAChannel(channel_id, client, gowa_manager)
