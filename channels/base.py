"""Channel contract (plano 02 Fase 0).

``Channel`` is the provider-agnostic interface the core talks to instead of the
GOWA client directly. Plugins import it from the STABLE path ``channels.base``.
This phase introduces the contract + an internal ``GOWAChannel`` adapter; GOWA is
not yet extracted to a plugin (that is Fase 3).
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import Optional

from channels.events import InboundEvent


@dataclasses.dataclass(frozen=True)
class MediaLimits:
    """Size/container constraints a provider enforces for a media kind.

    The generic sibling of :class:`VideoLimits` (which adds codec rules on top):
    the PROVIDER declares, the core only evaluates — see ``channels.media_limits``.
    A kind with no declared limits is never blocked (GOWA/Telegram accept anything
    the linked device accepts).

    ``extensions`` empty = any container. ``max_bytes`` 0 = no size cap.
    """
    max_bytes: int = 0
    extensions: tuple = ()


@dataclasses.dataclass(frozen=True)
class TemplateSpec:
    """As regras de FORMA de um template, declaradas pelo provider (plano 92 · F1).

    Irmão de :class:`MediaLimits`, mesma política: o PROVIDER declara os valores,
    o core só avalia (``app.services.template_service``) — nenhum ``if provider ==``
    e, principalmente, nenhum vocabulário de um provedor específico no core. O que
    é "categoria válida", "tipo de botão" ou "quantos MB o exemplo pode ter" é da
    API de quem envia, não do WhatsBot.

    ``template_spec = None`` (o default) significa **sem restrição**: o core deixa
    passar e quem recusa é o provedor, com a mensagem dele. É a escolha
    consciente de não recriar no core uma cópia velha das regras da Meta — um zip
    de plugin anterior a este plano simplesmente perde a validação antecipada, não
    a funcionalidade.

    Campo vazio (``frozenset()``/``0``) = aquela dimensão não é restringida.
    """
    categories: frozenset = frozenset()
    header_formats: frozenset = frozenset()
    button_types: frozenset = frozenset()
    # {tipo: máximo}, para provedores que limitam a MISTURA de botões.
    button_type_max: dict = dataclasses.field(default_factory=dict)
    button_text_max: int = 0
    buttons_max: int = 0
    # MIMEs aceitos como EXEMPLO de cabeçalho de mídia, e o teto em bytes.
    upload_mimes: frozenset = frozenset()
    upload_max_bytes: int = 0


@dataclasses.dataclass(frozen=True)
class AudioLimits:
    """The audio constraints a provider's API enforces — codec-aware.

    Same policy/mechanism split as :class:`VideoLimits`: the PROVIDER declares the
    containers/codecs/cap, the core only evaluates (``channels.audio_validate``)
    and, when the file does not conform, re-encodes it (``channels.audio_transcode``).
    A channel that declares a plain :class:`MediaLimits` for ``"audio"`` (or nothing)
    keeps the old extension+size behaviour — declaring ``AudioLimits`` is what opts
    a provider into codec inspection and transcoding.

    Why codecs matter: the WhatsApp Cloud API accepts ``audio/ogg`` **only with the
    OPUS codec** (Ogg/Vorbis is refused), so extension alone cannot tell a
    deliverable ``.ogg`` from a rejected one.

    ``codecs`` is the accepted set for containers with no specific rule;
    ``codecs_by_ext`` overrides it per container as ``((".ogg", ("opus",)), …)`` (a
    tuple of pairs, not a dict, to keep the dataclass frozen/hashable).
    ``transcode_targets`` is the provider's preferred OUTPUT container order for a
    re-encode; empty falls back to ``extensions``.
    """
    max_bytes: int = 0
    extensions: tuple = ()
    codecs: tuple = ()
    codecs_by_ext: tuple = ()
    transcode: bool = True
    transcode_targets: tuple = ()

    def codecs_for(self, ext: str) -> tuple:
        """Accepted codecs for a container, falling back to the flat ``codecs``."""
        ext = (ext or "").lower()
        for entry_ext, codecs in self.codecs_by_ext or ():
            if str(entry_ext).lower() == ext:
                return tuple(codecs or ())
        return tuple(self.codecs or ())


@dataclasses.dataclass(frozen=True)
class VideoLimits:
    """The video constraints a provider's API enforces (plano 65 — desacoplamento).

    POLICY lives here, declared BY THE PROVIDER; the MECHANISM (ffprobe validation
    + ffmpeg transcode) stays generic in ``channels.video_validate`` /
    ``channels.video_transcode``. The core never hardcodes a provider's numbers and
    never checks provider name — a channel whose capabilities declare no
    ``VideoLimits`` is simply not validated (GOWA/Telegram deliver arbitrary mp4).

    WhatsApp Cloud declares 16 MB / mp4-3gp / H.264 / AAC / one audio track; a
    future provider with different rules declares its own and everything downstream
    (validation messages, transcode bitrate target) follows.
    """
    max_bytes: int
    extensions: tuple = (".mp4",)
    video_codecs: tuple = ("h264",)
    audio_codecs: tuple = ("aac",)
    max_audio_streams: int = 1
    # Whether a non-conforming file may be re-encoded to fit instead of blocked.
    transcode: bool = True


@dataclasses.dataclass
class ChannelCapabilities:
    qr: bool = False
    templates: bool = False
    groups: bool = False
    presence: bool = False
    reactions: bool = False
    media: bool = False
    # Whether the provider can delete-for-everyone (revoke) a sent message. GOWA
    # can (``POST /message/{id}/revoke``); WhatsApp Cloud CANNOT (no Graph endpoint)
    # so it leaves this False and the panel hides "Apagar". Drives the UI by
    # CAPABILITY, never provider name.
    revoke: bool = False
    # Whether the provider can edit the text of an already-sent message. Both
    # WhatsApp providers can (GOWA ``/message/{id}/update``, Cloud messages endpoint
    # with ``message_id``); gates the "Editar" menu item.
    edit_message: bool = False
    inbound_route: str = "path"           # "path" | "poll" | "none"
    # Whether this provider's access token EXPIRES and must be renewed in the
    # background (plano 46 · 01-C, D8). ``False`` = the token never expires by time
    # (GOWA session, WhatsApp Cloud permanent token, Telegram bot token) and NO
    # refresh loop should run. ``True`` opts the provider into
    # :meth:`Channel.refresh_token_if_needed` being called periodically by the
    # plugin's own lifecycle loop (Instagram's 60-day user token, which dies
    # SILENTLY if not renewed). Capability-driven — the core never branches on
    # provider name, and a provider that leaves it False never starts a loop.
    token_refresh: bool = False
    # Customer-care session window in hours (plano 11 Fase 6). 0 = always-open
    # (GOWA/linked-device): free text any time. >0 = providers like the WhatsApp
    # Cloud API where free text is only allowed within N hours of the last inbound;
    # outside it requires an approved template (HSM). Drives the pipeline by
    # CAPABILITY, never by provider name.
    session_window_hours: int = 0
    # EXTENDED free-text window, in hours, that applies ONLY to a send made by a
    # HUMAN operator (plano 46 · 02.2). 0 = no extension (the ``session_window_
    # hours`` rule applies to everyone — WhatsApp Cloud, where outside the 24h only
    # an approved template is allowed). >0 = the provider has a human-agent escape
    # hatch beyond the normal window: Messenger/Instagram accept
    # ``messaging_type=MESSAGE_TAG`` + ``tag=HUMAN_AGENT`` for up to 7 days, but
    # ONLY for a real human reply — using it for the AI is a compliance tripwire.
    # The core evaluates it in ``OutboundRouter.session_open(..., by_human=True)``,
    # which only the operator-initiated send routes pass; the agentic auto-reply
    # never does. Capability-driven, never by provider name.
    human_window_hours: int = 0
    # Credential keys a channel of this provider MUST have to ever become
    # operational (plano 02 — anti zombie-channel). Empty for QR/linked-device
    # providers (GOWA): they legitimately bootstrap from an empty channel via the
    # QR connect flow. For credential-only providers without a connect step
    # (WhatsApp Cloud API, Telegram) a channel missing these can never connect nor
    # send — so ``create_channel`` rejects it. Drives validation by CAPABILITY,
    # never by provider name. Tuple (immutable) so it is safe as a default.
    required_credentials: tuple = ()
    # Per-media-kind constraints the provider's API enforces, e.g.
    # ``{"video": VideoLimits(max_bytes=16*1024*1024, ...)}`` (plano 65). The
    # PROVIDER declares, the core validates/transcodes generically — same pattern as
    # ``required_credentials``. ``None``/missing kind = no constraint for that kind.
    # Providers that predate this field fall back to the legacy Cloud defaults in
    # ``channels.video_validate`` when they are windowed (retrocompat).
    media_limits: Optional[dict] = None
    # Regras de forma dos templates (plano 92 · F1), quando ``templates=True``.
    # ``None`` = o core não restringe e o provedor é quem recusa.
    template_spec: Optional[TemplateSpec] = None


@dataclasses.dataclass
class SendResult:
    ok: bool
    external_msg_id: str = ""
    error: str = ""


@dataclasses.dataclass(frozen=True)
class AccountIdentity:
    """A provider's stable identity for the *account* a channel is bound to.

    This is the dedup key (plano 32): two enabled channels of the SAME provider
    that resolve to the SAME ``AccountIdentity`` are the same account connected
    twice — the core rejects the second. It is intentionally opaque to the core:
    the core only ever compares two identities for equality, never interprets
    them (no ``if provider ==`` anywhere in the dedup machinery).

    - ``kind`` — the *namespace* of the value (``"phone"``, ``"phone_number_id"``,
      ``"bot_id"``, ``"bot_token"``, …). The engine only ever compares identities
      whose ``kind`` matches, so a ``phone`` is never confused with an ``@lid`` or
      a ``bot_token``. A provider MUST use ONE consistent ``kind`` for a given
      account across both hooks (see ``identity_from_credentials`` /
      ``account_identity``) — otherwise the create-time and login-time identities
      would never dedup against each other.
    - ``value`` — the already-**canonical**, non-empty string form of the account
      id. "Canonical" means the provider has normalized away every representation
      difference (e.g. a BR phone's 12↔13-digit variants collapse to one chosen
      form) so the core can compare with plain equality. Never pass a raw,
      un-normalized, or possibly-empty value; return ``None`` from the hook
      instead when the identity is unknown.
    """

    kind: str
    value: str


class Channel(ABC):
    """A connected messaging channel for one account on one provider."""

    provider: str = "base"

    def __init__(self, channel_id: str,
                 capabilities: Optional[ChannelCapabilities] = None):
        self.channel_id = channel_id
        self.capabilities = capabilities or ChannelCapabilities()

    # ── Lifecycle ────────────────────────────────────────────────────
    def start(self) -> None:  # default no-op
        ...

    def stop(self) -> None:  # default no-op
        ...

    @abstractmethod
    def status(self) -> dict:
        """Return ``{connected, logged_in, needs_qr, error}``."""

    def get_qr(self) -> Optional[bytes]:
        return None

    # ── Provider self-description (descriptor — plano 33) ─────────────
    # A provider declares EVERYTHING the core needs to OFFER and RENDER it, so the
    # core never knows the provider by name (no ``if provider ==`` in the offer
    # list, the create/edit form, or the post-create UX). ``GET /api/channels/
    # providers`` returns one descriptor per REGISTERED provider; the frontend
    # builds the whole channel form from it. Adding a provider = ship a plugin
    # whose ``Channel`` subclass overrides this — the core is untouched.
    #
    # Shape (all keys optional except ``provider``; the service fills sensible
    # defaults + reconciles ``required`` against ``ChannelCapabilities``):
    #   {
    #     "provider": "telegram",            # == cls.provider
    #     "label": "Telegram",               # human name for the picker/badge
    #     "color": "purple",                 # semantic tint token (frontend maps it)
    #     "credential_fields": [             # secrets/ids stored in channel_credentials
    #        {"key","label","type","placeholder"?,"required"?,"help"?}
    #     ],
    #     "config_fields": [                 # non-secret settings stored in config
    #        {"key","label","type","options"?,"default"?,"placeholder"?,"help"?}
    #     ],
    #     "capabilities": {"needs_qr": bool, "templates": bool},
    #     "ai_sequential_default": bool,     # default for config.ai.ai_sequential_enabled
    #     "post_create": None | {...},       # declarative post-create UX (see below)
    #     "form_component": None | "/plugins/<id>/static/<x>.js",  # optional rich form
    #   }
    #
    # Field ``type`` values the generic form understands: ``text``, ``secret``,
    # ``token_suggest`` (text + a "suggest random token" button), ``multiselect``
    # (checkbox group over ``options=[{value,label,hint?}]``, seeded from
    # ``default``), ``generated`` (auto-filled read-only string, prefixed by
    # ``prefix``), ``bool`` (single checkbox, seeded from ``default``).
    # ``post_create`` kinds: ``{"kind":"webhook_url","path":...}``
    # (show a copiable inbound-webhook URL) and ``{"kind":"autoconfigure",
    # "endpoint":...}`` (POST the endpoint, show the returned webhook/long-poll
    # result). ``{channel_id}`` in a path/endpoint is substituted client-side.

    @classmethod
    def provider_descriptor(cls) -> dict:
        """Self-description used by the core to offer + render this provider.

        Default: a minimal, credential-less descriptor derived from the class. The
        service layer (:func:`app.services.channel_service.providers`) reconciles
        ``required`` credential fields against ``ChannelCapabilities.required_
        credentials`` afterwards, so a provider that only declares its required
        credentials (or nothing) still gets a usable generic form. Real providers
        override this to add labels, a colour, field placeholders and any rich
        post-create UX. Must be pure (no network/DB) — the core calls it in the
        request path.
        """
        return {
            "provider": cls.provider,
            "label": cls.provider,
            "color": "gray",
            "credential_fields": [],
            "config_fields": [],
            "capabilities": {"needs_qr": False, "templates": False},
            "ai_sequential_default": False,
            "contact_type": cls.contact_type(),
            "post_create": None,
            "form_component": None,
        }

    # ── Contact type (marca por canal — plano tipos-de-contato) ──────
    @classmethod
    def contact_type(cls) -> str:
        """The kind of contact this provider materializes on inbound.

        Stored on ``contacts.contact_type`` when the core first creates a contact
        for an inbound message, and used by the UI as a badge + a list filter
        dimension. WhatsApp providers (GOWA + Cloud) return ``"whatsapp"``,
        Telegram returns ``"telegram"``. Default ``"outros"`` — a provider that
        doesn't override still gets a valid, non-empty tag. Must be pure (no
        network/DB): the core calls it in the inbound path.
        """
        return "outros"

    @classmethod
    def can_initiate_conversation(cls) -> bool:
        """Whether an operator can START a NEW conversation on this provider.

        Default ``True``: providers addressable by a stable, operator-known id
        (WhatsApp phone, Telegram chat_id) let the "Nova conversa" picker begin a
        proactive outbound. Return ``False`` for providers whose recipient id only
        exists AFTER the customer reaches out — e.g. the website widget, whose
        ``wsess_…`` session is minted in the visitor's browser and can't be
        addressed cold. The core hides such providers from the start-conversation
        inbox picker (``GET /api/channels/connected``). Pure static provider trait
        (no network/DB), resolved by the core via the provider class — never by
        ``if provider ==``.
        """
        return True

    # ── source_id form (dedup key — plano 42) ────────────────────────
    @classmethod
    def source_id_for(cls, phone: str, is_group: bool) -> str:
        """The channel-native ``source_id`` for a contact's conversation (plano 42).

        This is the stable per-inbox resolution key stored in
        ``contact_inboxes.source_id``/``source_jid`` (the dedup key on
        ``(inbox_id, source_id)``). The core stores and compares it opaquely — each
        provider OWNS its own form, so ``ContactMemory`` never appends a WhatsApp
        suffix by hand (no ``if provider ==`` in the core). Must be pure (no
        network/DB). Default: the bare identifier, undecorated — a provider whose
        ids need no suffix (Telegram ``chat_id``, WhatsApp Cloud ``wa_id``) uses it
        as-is. GOWA overrides it to append the WhatsApp JID suffix."""
        return phone

    # ── Account identity contract (dedup — plano 32) ─────────────────
    # A provider declares HOW to identify the account a channel is bound to; the
    # core does the rest generically (compare, store, unique index, 409 on create,
    # logout on connect). Adding a new provider = implement one or two of these
    # thin hooks; the core never changes and never branches on provider name.
    #
    # Providers split by WHEN the identity is knowable:
    #   • at create — it is inside the submitted credential (WhatsApp Cloud's
    #     ``phone_number_id``, Telegram's ``bot_token``) → override
    #     ``identity_from_credentials``. The core calls it in create/update and
    #     returns 409 before persisting a duplicate.
    #   • only after connection — it appears once the session is live (GOWA's own
    #     phone number after the QR pairing) → override ``account_identity``. The
    #     status sweep reads it and, on a conflict, calls ``reject_duplicate``.
    # A provider may implement both (e.g. Telegram: ``bot_token`` at create AND a
    # live ``bot_id``) — but MUST keep the ``kind`` consistent so the two dedup
    # against each other. Both hooks default to ``None`` (unknown), so ``test`` and
    # any provider that opts out simply never dedups.

    @classmethod
    def identity_from_credentials(cls, creds: dict) -> Optional[AccountIdentity]:
        """The account identity derivable from submitted credentials, or ``None``.

        Override for providers whose identity lives in the credential and is thus
        known at create/update time (before any connection). Return a canonical,
        non-empty :class:`AccountIdentity` or ``None`` when the credentials don't
        determine one (missing/blank field). Must be pure (no network/DB) — the
        core calls it synchronously in the create/update path. Default: ``None``.

        Example (WhatsApp Cloud)::

            pid = (creds.get("phone_number_id") or "").strip()
            return AccountIdentity("phone_number_id", pid) if pid else None
        """
        return None

    def account_identity(self) -> Optional[AccountIdentity]:
        """This live channel's account identity, or ``None`` if not yet knowable.

        Override for providers whose identity only appears once the session is
        connected (e.g. GOWA's own phone number after the QR pairing). Return a
        canonical, non-empty :class:`AccountIdentity`, or ``None`` while unknown
        (not logged in, probe failed). The status sweep calls it periodically and,
        on a conflict with another channel, refuses via :meth:`reject_duplicate`.
        Default: ``None``.

        Example (GOWA)::

            num = digits(self._client.get_own_number())
            return AccountIdentity("phone", canonical_br(num)) if num else None
        """
        return None

    def reject_duplicate(self) -> None:
        """Undo a just-detected duplicate connection (plano 32 D7).

        Called by the sweep when :meth:`account_identity` collided with an existing
        channel: this channel just paired to an account already owned by another
        channel, so we sever it. Default: ``logout()`` (QR/linked-device providers
        like GOWA drop the freshly-paired session), falling back to ``stop()``.
        Providers may override for a cleaner teardown. Must not raise.
        """
        try:
            logout = getattr(self, "logout", None)
            if callable(logout):
                logout()
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            self.stop()
        except Exception:  # noqa: BLE001
            pass

    # Session actions — meaningful only for QR/linked-device providers (GOWA).
    # Default no-op so telegram/cloud/test don't have to implement them
    # (plano 27 D4).
    def reconnect(self) -> dict:  # default: unsupported
        return {"ok": False, "error": "não suportado"}

    def logout(self) -> dict:  # default: unsupported
        return {"ok": False, "error": "não suportado"}

    # ── Outbound ─────────────────────────────────────────────────────
    @abstractmethod
    def send_text(self, chat_id: str, text: str, *, reply_to=None,
                  mentions=None) -> SendResult: ...

    @abstractmethod
    def send_media(self, chat_id: str, kind: str, path_or_url: str, *,
                   caption: str = "", filename=None) -> SendResult: ...

    # Optional, default no-op
    def mark_read(self, chat_id: str, msg_id: str) -> None: ...
    def send_presence(self, chat_id: str, state: str) -> None: ...
    def react(self, chat_id: str, msg_id: str, emoji: str) -> None: ...
    def revoke(self, chat_id: str, msg_id: str) -> None: ...

    def edit_text(self, chat_id: str, msg_id: str, text: str) -> SendResult:
        """Edit the text of an already-sent message (plano — editar mensagem).

        Optional, capability-gated (``ChannelCapabilities.edit_message``). The
        default refuses so a provider that can't edit degrades cleanly instead of
        pretending to succeed. Providers that CAN edit (GOWA via
        ``/message/{id}/update``, WhatsApp Cloud via the messages endpoint with
        ``message_id``) override this. Driven by CAPABILITY, never provider name."""
        return SendResult(ok=False, error="edit_not_supported")

    def fetch_avatar(self, chat_id: str) -> Optional[bytes]:
        """Current profile photo bytes for ``chat_id`` (plano 38 F5), or ``None``.

        Optional hook: avatar caching is a per-provider feature. The default returns
        ``None`` — the ``None`` IS the gate, so a provider that doesn't implement it
        (Telegram/Cloud) never triggers a GOWA fetch and the cache is left untouched.
        GOWA overrides it (calls its device-scoped ``get_avatar``)."""
        return None

    def send_template(self, *args, **kwargs) -> SendResult:
        raise NotImplementedError(f"{self.provider} does not support templates")

    def list_templates(self) -> list[dict]:
        """List message templates (HSM) for the account, any status.

        Optional, default raises NotImplementedError (like ``send_template``). Only
        channels with ``capabilities.templates`` implement it. Each item is
        normalized to ``{name, language, category, status, components:[...]}``.
        """
        raise NotImplementedError(f"{self.provider} does not support listing templates")

    def create_template(self, name: str, *, category: str, language: str,
                        body_text: str, header_text: Optional[str] = None,
                        footer_text: Optional[str] = None,
                        body_examples: Optional[list] = None,
                        header_examples: Optional[list] = None,
                        header_format: Optional[str] = None,
                        header_handle: Optional[str] = None,
                        buttons: Optional[list] = None) -> dict:
        """Create a message template (HSM) at the provider, returning the raw result.

        Optional, default raises NotImplementedError. Only channels with
        ``capabilities.templates`` implement it. Returns
        ``{ok, id?, status?, category?, error?}``.

        Media header (plano 73): ``header_format`` in ``{IMAGE, VIDEO, DOCUMENT}``
        plus a ``header_handle`` produced by :meth:`upload_example` replaces the
        text header. ``buttons`` is a already-typed list of
        ``{type, text?, url?, phone_number?, example?}`` (types QUICK_REPLY / URL /
        PHONE_NUMBER / COPY_CODE) — the provider converts it to its own wire shape.
        Both are optional: omitting them keeps the text-only behaviour unchanged.
        """
        raise NotImplementedError(f"{self.provider} does not support creating templates")

    def upload_example(self, file_bytes: bytes, mime: str,
                       filename: str) -> dict:
        """Upload a sample media file used as a template's header example.

        Optional (plano 73), default refuses so a provider without the concept
        degrades cleanly. Returns ``{ok, handle?, error?}`` — the ``handle`` is then
        passed back as ``header_handle`` to :meth:`create_template`.
        """
        return {"ok": False, "error": "not_supported"}

    def delete_template(self, name: str) -> dict:
        """Delete a message template (all language versions) by name.

        Optional, default raises NotImplementedError. Returns ``{ok, error?}``.
        """
        raise NotImplementedError(f"{self.provider} does not support deleting templates")

    def check_phone(self, phone: str) -> dict:
        """Whether ``phone`` is reachable on this channel.

        Default for providers that CANNOT verify a number before sending (WhatsApp
        Cloud API, Telegram, …): assume it's valid. Verification only exists on
        linked-device providers (GOWA, via ``/user/check``), which override this.
        Returns the GOWA-shaped dict (``registered``, ``canonical_phone``, ``name``)
        so the caller stays provider-agnostic — never branches on provider name.
        """
        return {"registered": True, "canonical_phone": phone, "name": ""}

    # ── Token lifecycle (plano 46 · 01-C, D8) ────────────────────────
    def refresh_token_if_needed(self) -> None:
        """Renew this channel's access token when it is close to expiring.

        Opt-in hook, gated by ``ChannelCapabilities.token_refresh``: the CORE never
        schedules it — the provider's own plugin registers a supervised loop in its
        ``lifecycle.setup(ctx)`` (``ctx.spawn_task("token_refresh", loop)``, molde
        ``telegram/lifecycle.py``) that walks its channels every N minutes and calls
        this. The supervisor cancels the task on plugin disable (``stop_owner``), so
        a provider that does not declare the capability never starts a loop at all.

        Contract for an implementation (Instagram's 60-day token is the reference —
        it dies SILENTLY if not renewed):

        * Only refresh a token that is still VALID (an expired one cannot be
          exchanged) — a dead token must surface as an error, not a retry storm.
        * Respect the provider's minimum age (IG: the token must be ≥24h old) and
          only act when it is close to expiring (IG: <10 days left).
        * Persist BOTH the new token and its expiry via
          ``registry.set_credential(channel_id, "access_token"/"expires_at", …)``
          so the value survives a restart.
        * Never raise: the loop must survive a provider outage. Log and return.

        Default: no-op (the token never expires by time).
        """

    # ── Inbound ──────────────────────────────────────────────────────
    def verify_inbound_signature(self, raw_body: bytes, headers) -> bool:
        """Whether an inbound webhook POST is authentic (plano 46 · 01-A, D3).

        The core calls this in ``POST /api/webhook/{provider}/{channel_id}`` with
        the **RAW** request body (never a re-serialized dict — re-encoding JSON
        changes the bytes and breaks any HMAC) and the request headers, BEFORE
        ``parse_inbound``. Returning ``False`` makes the core drop the payload
        without ingesting anything (it still answers 200, so the provider does not
        retry a forged request forever).

        Default: ``True`` — no verification. Providers whose transport carries no
        signature (GOWA's local subprocess, Telegram long-poll) keep the exact
        pre-existing behaviour. Meta providers override it to validate
        ``X-Hub-Signature-256`` = ``sha256=HMAC_SHA256(app_secret, raw_body)``, and
        treat a channel with NO ``app_secret`` configured as "not verifying" (return
        ``True`` + warn) so a half-configured channel keeps working. Must be pure
        of DB writes and fast — it runs in the request path.
        """
        return True

    def verify_inbound_signature_result(self, raw_body: bytes,
                                        headers) -> tuple[bool, bool]:
        """Return ``(accepted, authenticated)`` for one atomic verification.

        ``verify_inbound_signature`` may deliberately accept unsigned traffic for
        transports without signatures and for legacy channels missing a newly
        introduced secret. Filters with external side effects need to distinguish
        that compatibility path from a cryptographically authenticated body.

        The two facts belong to ONE verdict: reading a mutable credential again
        after verification creates a TOCTOU window where a secret added between
        the reads could label an unsigned payload as authenticated. The default
        delegates the accept/reject decision to the legacy hook and conservatively
        reports ``authenticated=False``. Providers that expose authenticated
        provenance override this method, snapshot their secret once and derive
        both booleans from that same snapshot.
        """
        return bool(self.verify_inbound_signature(raw_body, headers)), False

    @abstractmethod
    def parse_inbound(self, raw: dict) -> list[InboundEvent]:
        """Translate a provider-specific raw payload into InboundEvents."""
