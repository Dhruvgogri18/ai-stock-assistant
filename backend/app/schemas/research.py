"""HTTP schemas for the direct research endpoints."""
from pydantic import BaseModel


class Filter(BaseModel):
    metric: str
    op: str
    value: float


class SymbolsBody(BaseModel):
    symbols: list[str]
