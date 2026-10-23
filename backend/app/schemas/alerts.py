"""HTTP schemas for alerts, notifications and the calendar page."""
from typing import Any

from pydantic import BaseModel, Field


class AlertCreate(BaseModel):
    kind: str                                   # breakout | result
    preset: str | None = None                   # breakout only
    symbol: str | None = None                   # None = whole watchlist
    params: dict[str, Any] = Field(default_factory=dict)
    include_existing: bool = False              # result alerts: also report results already announced


class AlertUpdate(BaseModel):
    active: bool


class ReadBody(BaseModel):
    ids: list[int] | None = None                # None = mark everything read
