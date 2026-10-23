"""Screener page: curated + saved screeners, the metric catalog for "Refine Screener", and ad-hoc runs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.app.repositories import screener_repository as repo
from backend.app.schemas.screener import CreateBody, FromToolBody, RunBody, UpdateBody
from backend.app.services.screener import catalog as cat, engine

router = APIRouter(prefix="/screeners", tags=["screeners"])
READ_ONLY = "Curated screeners are read-only. Save a copy to change it."


def _curated_card(c: dict) -> dict:
    return {"id": c["id"], "name": c["name"], "description": c["description"], "category": c["category"],
            "curated": True, "preview": engine.preview(c["definition"])}


def _saved_card(s: dict) -> dict:
    return {"id": s["id"], "name": s["name"], "description": s["description"], "source": s["source"],
            "curated": False, "updated_at": s["updated_at"], "preview": engine.preview(s["definition"])}


def _resolve(sid: str) -> dict:
    """A curated or saved screener as {id, name, description, definition, curated, ...}."""
    if sid.startswith("c-"):
        c = cat.CURATED_BY_ID.get(sid)
        if not c:
            raise HTTPException(404, "Screener not found")
        return {"id": c["id"], "name": c["name"], "description": c["description"], "category": c["category"],
                "definition": c["definition"], "curated": True}
    s = repo.get(sid)
    if not s:
        raise HTTPException(404, "Screener not found")
    return {**s, "curated": False}


@router.get("/metrics")
def metrics() -> dict:
    """Every column/filter the Refine panel can offer, with how many stocks currently have a value."""
    cov = engine.coverage()
    items = [{**m.public(), "coverage": cov["coverage"][m.key]} for m in cat.METRICS]
    return {"groups": [{"name": n, "description": d} for n, d in cat.GROUPS], "metrics": items,
            "universe": cov["universe"], "default_columns": cat.DEFAULT_COLUMNS, "as_of": cov["as_of"]}


@router.get("/curated")
def curated() -> dict:
    cards = [_curated_card(c) for c in cat.CURATED]
    return {"items": cards, "categories": list(dict.fromkeys(c["category"] for c in cards))}


@router.get("/mine")
def mine() -> dict:
    return {"items": [_saved_card(s) for s in repo.list_all()]}


@router.post("/run")
def run(body: RunBody) -> dict:
    return engine.run(body.definition, body.limit, body.offset)


@router.post("/from-tool", status_code=201)
def from_tool(body: FromToolBody) -> dict:
    """Save a chat result (movers / screener tool call) as a screener and return it."""
    name, desc, definition = engine.from_tool(body.tool, body.args)
    return repo.create(name, desc, definition, source="chat")


@router.post("", status_code=201)
def create(body: CreateBody) -> dict:
    d = engine.normalize(body.definition)
    return repo.create(body.name.strip(), body.description.strip(), d, source=body.source if body.source in ("custom", "chat", "curated") else "custom")


@router.get("/{sid}")
def detail(sid: str, limit: int = 50, offset: int = 0) -> dict:
    s = _resolve(sid)
    return {"screener": s, "results": engine.run(s["definition"], limit, offset)}


@router.patch("/{sid}")
def update(sid: str, body: UpdateBody) -> dict:
    if sid.startswith("c-"):
        raise HTTPException(403, READ_ONLY)
    d = engine.normalize(body.definition) if body.definition is not None else None
    s = repo.update(sid, body.name.strip() if body.name else None, body.description.strip() if body.description is not None else None, d)
    if not s:
        raise HTTPException(404, "Screener not found")
    return s


@router.delete("/{sid}")
def delete(sid: str) -> dict:
    if sid.startswith("c-"):
        raise HTTPException(403, READ_ONLY)
    if not repo.delete(sid):
        raise HTTPException(404, "Screener not found")
    return {"deleted": sid}


@router.post("/{sid}/duplicate", status_code=201)
def duplicate(sid: str) -> dict:
    s = _resolve(sid)
    name = s["name"] if s["curated"] else f"{s['name']} (copy)"
    return repo.create(name, s["description"], s["definition"], source="curated" if s["curated"] else s.get("source", "custom"))
