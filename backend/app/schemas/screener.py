"""HTTP schemas for the screener page endpoints. Definitions are validated by services/screener/engine.normalize."""
from typing import Any

from pydantic import BaseModel, Field


class RunBody(BaseModel):
    definition: dict[str, Any] = Field(default_factory=dict)
    limit: int = 50
    offset: int = 0


class CreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=400)
    definition: dict[str, Any] = Field(default_factory=dict)
    source: str = "custom"


class UpdateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=400)
    definition: dict[str, Any] | None = None


class FromToolBody(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
