"""The ONLY place the app talks to the LLM. Every call made here is logged
(full prompt, completion or tool calls, tokens, latency, errors) through llm_trace.

Works with any OpenAI-compatible gateway (LLM_BASE_URL).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from openai import OpenAI

from backend.app.config.settings import settings
from backend.app.services import llm_trace


class LLMError(RuntimeError):
    """The gateway call failed (already logged)."""


@dataclass
class ChatResult:
    content: str | None
    tool_calls: list[dict] = field(default_factory=list)  # [{"id","name","arguments"}]
    usage: dict = field(default_factory=dict)
    finish_reason: str | None = None
    model: str | None = None

    @property
    def assistant_message(self) -> dict:
        """The assistant turn, ready to append to `messages` before sending tool results."""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": t["id"], "type": "function", "function": {"name": t["name"], "arguments": t["arguments"]}}
                for t in self.tool_calls
            ]
        return msg


@dataclass(frozen=True)
class Provider:
    name: str
    api_key: str
    base_url: str
    model: str
    fast_model: str


def _providers() -> list[Provider]:
    """Primary gateway first, then the free fallbacks. Only providers with a key and model are included."""
    s = settings
    candidates = [
        Provider("gateway", s.llm_api_key, s.llm_base_url, s.llm_model, s.llm_fast_model or s.llm_model),
        Provider("fallback", s.fallback_llm_api_key, s.fallback_llm_base_url, s.fallback_llm_model,
                 s.fallback_llm_fast_model or s.fallback_llm_model),
        Provider("fallback2", s.fallback2_llm_api_key, s.fallback2_llm_base_url, s.fallback2_llm_model,
                 s.fallback2_llm_fast_model or s.fallback2_llm_model),
    ]
    return [p for p in candidates if p.api_key and p.model]


def configured() -> bool:
    return bool(_providers())


def fast_model() -> str:
    """Fast model of the first available provider (used only as a hint; each provider maps it to its own)."""
    p = _providers()
    return p[0].fast_model if p else ""


@lru_cache(maxsize=8)
def _client(api_key: str, base_url: str, timeout: float) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)


def _call(p: Provider, kwargs: dict, temperature: float | None):
    client = _client(p.api_key, p.base_url, settings.llm_timeout)
    try:
        return client.chat.completions.create(**kwargs)
    except Exception as exc:
        # Some reasoning models reject `temperature`; retry once without it.
        if temperature is not None and "temperature" in str(exc).lower():
            kwargs.pop("temperature", None)
            return client.chat.completions.create(**kwargs)
        raise


def chat_completion(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float | None = None,
    tools: list[dict] | None = None,
    max_tokens: int | None = None,
) -> ChatResult:
    providers = _providers()
    if not providers:
        raise LLMError("No LLM configured: set LLM_API_KEY/LLM_MODEL or FALLBACK_LLM_API_KEY in .env")
    want_fast = bool(model) and model == fast_model() and model != providers[0].model

    errors: list[str] = []
    resp = None
    used_model = ""
    for p in providers:
        used_model = p.fast_model if want_fast else p.model
        handle = llm_trace.log_llm_call(messages=messages, model=used_model, temperature=temperature, tools=tools)
        kwargs: dict[str, Any] = {"model": used_model, "messages": messages,
                                  "max_tokens": max_tokens or settings.llm_max_tokens}
        if tools:
            kwargs["tools"] = tools
        if temperature is not None:
            kwargs["temperature"] = temperature
        try:
            resp = _call(p, kwargs, temperature)
            break
        except Exception as exc:  # noqa: BLE001  (fall through to the next provider)
            msg = f"{p.name}: {type(exc).__name__}: {exc}"
            llm_trace.log_llm_response(handle=handle, error=msg)
            errors.append(msg[:300])
    if resp is None:
        raise LLMError(" | ".join(errors))
    model = used_model

    choice = resp.choices[0]
    msg = choice.message
    tool_calls = [
        {"id": t.id, "name": t.function.name, "arguments": t.function.arguments or "{}"}
        for t in (getattr(msg, "tool_calls", None) or [])
    ]
    u = getattr(resp, "usage", None)
    usage = (
        {"prompt_tokens": getattr(u, "prompt_tokens", None), "completion_tokens": getattr(u, "completion_tokens", None)}
        if u else None
    )
    used = llm_trace.log_llm_response(
        handle=handle, content=msg.content, tool_calls=tool_calls or None, usage=usage,
        model=getattr(resp, "model", None) or model, finish_reason=choice.finish_reason,
    )
    return ChatResult(content=msg.content, tool_calls=tool_calls, usage=used,
                      finish_reason=choice.finish_reason, model=model)


def chat_json(messages: list[dict], **kw) -> dict:
    """Chat call that must return a JSON object (code fences tolerated). Raises ValueError if it isn't."""
    text = (chat_completion(messages, **kw).content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"model did not return JSON: {text[:200]!r}")
    return json.loads(text[start : end + 1])
