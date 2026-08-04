"""DB-free channel-provider doubles shared by core contract tests.

``FakeChannel`` exercises the generic :class:`channels.base.Channel` contract.  It
is deliberately separate from :class:`tests.fakes.FakeGowaClient`: GOWA still has
a provider-specific construction seam in ``ChannelRegistry``, so pretending its
HTTP client is a ``Channel`` would hide bugs instead of removing duplication.

Use :meth:`FakeChannel.configured` when a test needs provider-level traits (the
classmethod contracts), and constructor arguments for per-channel runtime state::

    CloudFake = FakeChannel.configured(
        provider="fake_cloud",
        contact_type="whatsapp",
        credential_identity_key="phone_number_id",
        identity_kind="phone_number_id",
        capabilities=ChannelCapabilities(session_window_hours=24),
    )
    channel = CloudFake("cloud-1", live_identity="123")

The synthetic inbound wire shape is a plain mapping.  A single event may be
passed directly, or several under ``{"events": [...]}``.  The supported kinds
match the subset needed by the core-provider contract suite: ``message``,
``system``, ``receipt``, ``reaction`` and ``edited``.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any, ClassVar

from channels.base import AccountIdentity, Channel, ChannelCapabilities, SendResult
from channels.events import InboundEvent
from tests.fakes import FakeGowaClient


_UNSET = object()


def _copy_capabilities(
    value: ChannelCapabilities | Mapping[str, Any] | None,
) -> ChannelCapabilities:
    if value is None:
        return ChannelCapabilities()
    if isinstance(value, ChannelCapabilities):
        return copy.deepcopy(value)
    if isinstance(value, Mapping):
        return ChannelCapabilities(**copy.deepcopy(dict(value)))
    raise TypeError("capabilities must be ChannelCapabilities, a mapping, or None")


class FakeChannel(Channel):
    """Configurable, inspectable implementation of the generic channel contract.

    Provider-level behavior is held in class attributes so classmethods behave as
    they do on real providers.  Prefer :meth:`configured` over mutating those
    attributes directly; every call creates an isolated subclass.
    """

    provider = "fake"
    supported_inbound_kinds: ClassVar[frozenset[str]] = frozenset(
        {"message", "system", "receipt", "reaction", "edited"}
    )

    _contact_type_value: ClassVar[str] = "outros"
    _descriptor_overrides: ClassVar[dict[str, Any]] = {}
    _default_capabilities: ClassVar[ChannelCapabilities] = ChannelCapabilities(
        reactions=True,
        media=True,
        inbound_route="path",
    )
    _source_id_builder: ClassVar[Callable[[str, bool], str]] = staticmethod(
        lambda phone, is_group: str(phone)
    )
    _credential_identity_key: ClassVar[str | None] = "account_id"
    _identity_kind: ClassVar[str] = "fake_account"
    _identity_normalizer: ClassVar[Callable[[Any], str]] = staticmethod(
        lambda value: str(value).strip()
    )

    @classmethod
    def configured(
        cls,
        *,
        name: str | None = None,
        provider: str | None = None,
        descriptor: Mapping[str, Any] | None = None,
        contact_type: str | None = None,
        source_id_builder: Callable[[str, bool], str] | None = None,
        credential_identity_key: str | None | object = _UNSET,
        identity_kind: str | None = None,
        identity_normalizer: Callable[[Any], str] | None = None,
        capabilities: ChannelCapabilities | Mapping[str, Any] | None = None,
    ) -> type["FakeChannel"]:
        """Return an isolated fake-provider subclass with static traits applied."""

        provider_value = provider or cls.provider
        attrs: dict[str, Any] = {
            "__module__": cls.__module__,
            "provider": provider_value,
            "_descriptor_overrides": copy.deepcopy(
                dict(descriptor) if descriptor is not None
                else cls._descriptor_overrides
            ),
            "_default_capabilities": _copy_capabilities(
                capabilities if capabilities is not None
                else cls._default_capabilities
            ),
        }
        if contact_type is not None:
            attrs["_contact_type_value"] = str(contact_type)
        if source_id_builder is not None:
            attrs["_source_id_builder"] = staticmethod(source_id_builder)
        if credential_identity_key is not _UNSET:
            attrs["_credential_identity_key"] = credential_identity_key
        if identity_kind is not None:
            attrs["_identity_kind"] = str(identity_kind)
        if identity_normalizer is not None:
            attrs["_identity_normalizer"] = staticmethod(identity_normalizer)

        class_name = name or "".join(
            part.capitalize() for part in provider_value.split("_")
        ) + "FakeChannel"
        return type(class_name, (cls,), attrs)

    def __init__(
        self,
        channel_id: str,
        registry: Any = None,
        credentials: Mapping[str, Any] | None = None,
        *,
        capabilities: ChannelCapabilities | Mapping[str, Any] | None = None,
        media_limits: Mapping[str, Any] | None | object = _UNSET,
        session_window_hours: int | None = None,
        human_window_hours: int | None = None,
        status: Mapping[str, Any] | None = None,
        live_identity: AccountIdentity | str | None = None,
        send_outcomes: Mapping[str, SendResult] | None = None,
        external_msg_id_prefix: str = "fake_out",
    ) -> None:
        caps = _copy_capabilities(
            capabilities if capabilities is not None
            else type(self)._default_capabilities
        )
        if media_limits is not _UNSET:
            caps.media_limits = copy.deepcopy(media_limits)
        if session_window_hours is not None:
            caps.session_window_hours = int(session_window_hours)
        if human_window_hours is not None:
            caps.human_window_hours = int(human_window_hours)
        super().__init__(channel_id, caps)

        self.registry = registry
        self.credentials = dict(credentials or {})
        self._status = {
            "connected": True,
            "logged_in": True,
            "needs_qr": bool(caps.qr),
            "error": None,
        }
        if status is not None:
            self._status.update(copy.deepcopy(dict(status)))

        if isinstance(live_identity, str):
            normalized = type(self)._identity_normalizer(live_identity)
            self._live_identity = (
                AccountIdentity(type(self)._identity_kind, normalized)
                if normalized else None
            )
        else:
            self._live_identity = copy.deepcopy(live_identity)

        self._send_outcomes = {
            str(kind): copy.deepcopy(result)
            for kind, result in (send_outcomes or {}).items()
        }
        self.external_msg_id_prefix = external_msg_id_prefix
        self.status_calls = 0
        self.duplicate_rejections = 0
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    # -- provider-level contracts -----------------------------------------

    @classmethod
    def provider_descriptor(cls) -> dict:
        descriptor = super().provider_descriptor()
        overrides = copy.deepcopy(cls._descriptor_overrides)
        capability_overrides = overrides.pop("capabilities", {}) or {}
        descriptor.update(overrides)
        descriptor["provider"] = cls.provider
        descriptor["contact_type"] = cls.contact_type()
        descriptor["capabilities"] = {
            "needs_qr": bool(cls._default_capabilities.qr),
            "templates": bool(cls._default_capabilities.templates),
            **copy.deepcopy(dict(capability_overrides)),
        }
        return descriptor

    @classmethod
    def contact_type(cls) -> str:
        return cls._contact_type_value

    @classmethod
    def source_id_for(cls, phone: str, is_group: bool) -> str:
        return str(cls._source_id_builder(phone, is_group))

    @classmethod
    def identity_from_credentials(
        cls, creds: dict,
    ) -> AccountIdentity | None:
        key = cls._credential_identity_key
        if not key:
            return None
        value = cls._identity_normalizer((creds or {}).get(key, ""))
        if not value:
            return None
        return AccountIdentity(cls._identity_kind, value)

    # -- lifecycle / live identity ---------------------------------------

    def start(self) -> None:
        self.calls.append(("start", {}))
        self._status.update(connected=True, logged_in=True, error=None)

    def stop(self) -> None:
        self.calls.append(("stop", {}))
        self._status.update(connected=False, logged_in=False)

    def reconnect(self) -> dict:
        self.calls.append(("reconnect", {}))
        self._status.update(connected=True, logged_in=True, error=None)
        return {"ok": True}

    def logout(self) -> dict:
        self.calls.append(("logout", {}))
        self._status.update(connected=False, logged_in=False)
        return {"ok": True}

    def status(self) -> dict:
        self.status_calls += 1
        return copy.deepcopy(self._status)

    def account_identity(self) -> AccountIdentity | None:
        return copy.deepcopy(self._live_identity)

    def reject_duplicate(self) -> None:
        self.duplicate_rejections += 1
        self.calls.append(("reject_duplicate", {}))
        self.logout()

    # -- outbound ---------------------------------------------------------

    def _send_result(self, kind: str) -> SendResult:
        configured = self._send_outcomes.get(kind)
        if configured is None:
            configured = self._send_outcomes.get("*")
        if configured is not None:
            result = copy.deepcopy(configured)
            if result.ok and not result.external_msg_id:
                result.external_msg_id = (
                    f"{self.external_msg_id_prefix}_{len(self.sent)}"
                )
            return result
        return SendResult(
            ok=True,
            external_msg_id=f"{self.external_msg_id_prefix}_{len(self.sent)}",
        )

    def send_text(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to=None,
        mentions=None,
    ) -> SendResult:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "reply_to": reply_to,
            "mentions": copy.deepcopy(mentions),
        }
        self.sent.append(("text", payload))
        return self._send_result("text")

    def send_media(
        self,
        chat_id: str,
        kind: str,
        path_or_url: str,
        *,
        caption: str = "",
        filename=None,
    ) -> SendResult:
        payload = {
            "chat_id": chat_id,
            "kind": kind,
            "path_or_url": path_or_url,
            "caption": caption,
            "filename": filename,
        }
        self.sent.append(("media", payload))
        return self._send_result("media")

    def edit_text(self, chat_id: str, msg_id: str, text: str) -> SendResult:
        payload = {"chat_id": chat_id, "msg_id": msg_id, "text": text}
        self.sent.append(("edit", payload))
        return self._send_result("edit")

    def mark_read(self, chat_id: str, msg_id: str) -> None:
        self.calls.append(("mark_read", {"chat_id": chat_id, "msg_id": msg_id}))

    def send_presence(self, chat_id: str, state: str) -> None:
        self.calls.append(("presence", {"chat_id": chat_id, "state": state}))

    def react(self, chat_id: str, msg_id: str, emoji: str) -> None:
        self.calls.append(("reaction", {
            "chat_id": chat_id, "msg_id": msg_id, "emoji": emoji,
        }))

    def revoke(self, chat_id: str, msg_id: str) -> None:
        self.calls.append(("revoke", {"chat_id": chat_id, "msg_id": msg_id}))

    # -- inbound ----------------------------------------------------------

    def parse_inbound(self, raw: dict) -> list[InboundEvent]:
        if not isinstance(raw, Mapping):
            raise TypeError("fake inbound payload must be a mapping")

        items = raw.get("events")
        if items is None:
            items = [raw]
        if not isinstance(items, (list, tuple)):
            raise TypeError("fake inbound 'events' must be a list or tuple")

        events: list[InboundEvent] = []
        for item in items:
            if not isinstance(item, Mapping):
                raise TypeError("each fake inbound event must be a mapping")
            data = dict(item)
            kind = str(data.get("kind") or "message")
            if kind not in self.supported_inbound_kinds:
                allowed = ", ".join(sorted(self.supported_inbound_kinds))
                raise ValueError(
                    f"unsupported fake inbound kind {kind!r}; expected one of {allowed}"
                )

            extras_raw = data.get("media_extras") or {}
            if not isinstance(extras_raw, Mapping):
                raise TypeError("fake inbound media_extras must be a mapping")
            extras = copy.deepcopy(dict(extras_raw))
            promoted = {
                "receipt": ("status", "errors", "msg_ids"),
                "reaction": ("emoji", "reacted_message_id", "is_from_me"),
                "edited": ("original_message_id", "is_from_me"),
                "system": ("system_type", "wa_id", "body"),
            }
            for key in promoted.get(kind, ()):
                if key in data:
                    extras[key] = copy.deepcopy(data[key])

            chat_id = str(data.get("chat_id") or data.get("from") or "")
            sender_id = str(data.get("sender_id") or data.get("from") or chat_id)
            event_raw = data.get("raw", data)
            if not isinstance(event_raw, Mapping):
                raise TypeError("fake inbound raw field must be a mapping")

            events.append(InboundEvent(
                channel_id=self.channel_id,
                provider=type(self).provider,
                kind=kind,
                direction=str(data.get("direction") or "in"),
                external_msg_id=str(
                    data.get("external_msg_id") or data.get("msg_id")
                    or data.get("id") or ""
                ),
                chat_id=chat_id,
                sender_id=sender_id,
                sender_name=str(data.get("sender_name") or data.get("from_name") or ""),
                is_group=bool(data.get("is_group", False)),
                text=str(data.get("text") or data.get("body") or ""),
                media_type=data.get("media_type"),
                media_path=data.get("media_path"),
                media_extras=extras,
                ts=float(data.get("ts") or data.get("timestamp") or 0.0),
                raw=copy.deepcopy(dict(event_raw)),
                display_text=str(data.get("display_text") or ""),
                trigger_ai=bool(data.get("trigger_ai", True)),
                reply_to_msg_id=data.get("reply_to_msg_id"),
                mentioned=bool(data.get("mentioned", False)),
                group_name=data.get("group_name"),
                can_send=data.get("can_send"),
                is_archived=data.get("is_archived"),
            ))
        return events


__all__ = ["FakeChannel", "FakeGowaClient"]
