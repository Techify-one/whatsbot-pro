"""Domain result for atomic routing of a conversation to a team."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RoutingSnapshot:
    team_id: int | None
    assignee_user_id: int | None
    active_agent_key: str | None
    ai_active: bool


@dataclass(frozen=True)
class TeamRoutingResult:
    conversation_id: int
    inbox_id: int
    team_id: int
    strategy: str
    before: RoutingSnapshot
    after: RoutingSnapshot
    fallback_reason: str | None = None

    @property
    def used_fallback(self) -> bool:
        return self.fallback_reason is not None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["used_fallback"] = self.used_fallback
        return data


class TeamRoutingError(ValueError):
    """A routing request whose identity cannot be materialized safely."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

