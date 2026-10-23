"""Smoke-test the gateway: python -m scripts.check_llm   (writes logs/sessions/check-llm.log)"""
from backend.app.services import llm_trace
from backend.app.services.llm import gateway_client as gw

if not gw.configured():
    raise SystemExit("Set LLM_API_KEY and LLM_MODEL in .env first.")
llm_trace.start_request(session_id="check-llm", user_message="ping")
with llm_trace.llm_stage("check"):
    r = gw.chat_completion([{"role": "user", "content": "Reply with exactly: ok"}], max_tokens=20)
print("reply:", r.content, "| usage:", r.usage, "| model:", r.model)
print("totals:", llm_trace.end_request(status="ok"))
print("log:", llm_trace.session_log_path("check-llm"))
