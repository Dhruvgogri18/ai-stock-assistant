import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeGateway:
    """Scripted stand-in for the OpenAI client: pops one canned reply per call and records requests."""

    def __init__(self):
        self.script, self.requests = [], []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def say(self, content=None, tool_calls=None, usage=(100, 20)):
        self.script.append((content, tool_calls or [], usage))

    def _create(self, **kw):
        self.requests.append(kw)
        content, calls, usage = self.script.pop(0)
        if isinstance(content, Exception):
            raise content
        tcs = [SimpleNamespace(id=f"call_{i}", function=SimpleNamespace(name=n, arguments=json.dumps(a)))
               for i, (n, a) in enumerate(calls)]
        msg = SimpleNamespace(content=content, tool_calls=tcs or None)
        u = SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1]) if usage else None
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls" if tcs else "stop")],
                               usage=u, model="fake-model")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from backend.app.config.settings import settings
    from backend.app.repositories import _sessions_db
    from backend.app.services.llm import gateway_client as gw
    from backend.app.services.orchestration import tools

    monkeypatch.setattr(settings, "session_db_path", str(tmp_path / "sessions.db"))
    monkeypatch.setattr(settings, "llm_log_dir", str(tmp_path / "logs"))
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "stock.db"))     # never touch the real warehouse
    from backend.app.repositories import bars_repository
    bars_repository._ready.clear()
    monkeypatch.setattr(settings, "llm_api_key", "k")
    monkeypatch.setattr(settings, "llm_model", "fake-model")
    monkeypatch.setattr(settings, "router_enabled", True)
    _sessions_db._initialised.clear()

    fake = FakeGateway()
    monkeypatch.setattr(gw, "_client", lambda *a, **k: fake)

    def fake_tool(name, args):
        if name == "boom":
            raise RuntimeError("x")
        return {"kv": {"P/E": 25.0}, "source": "FakeSource", "as_of": "2026-10-05", "provider": "fake", "args": args}

    monkeypatch.setattr(tools, "run_tool", fake_tool)
    return SimpleNamespace(fake=fake, tmp=tmp_path, settings=settings)
