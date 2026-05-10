"""Example plugin settings (Pydantic Valves)."""

from pydantic import BaseModel, Field


class Settings(BaseModel):
    welcome_text: str = Field(
        default="Olá!",
        description="Texto que pode ser exibido na tela do plugin.",
    )
    max_pings_per_contact: int = Field(
        default=100,
        description="Limite informativo de pings por contato.",
        ge=1,
        le=10_000,
    )
