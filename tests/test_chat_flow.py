import json

from fastapi.testclient import TestClient

ROUTE_RESEARCH = json.dumps({"route": "research", "topic": "TCS Peer Comparison", "title": "TCS vs IT peers", "rationale": "x"})
ANSWER = '# 📊 TCS vs peers\n\n> TCS is cheaper.\n\n<follow_ups>\n["What about HCL?", "Show TCS margins"]\n</follow_ups>'


def client():
    from backend.app.main import app
    return TestClient(app)


def test_full_turn_persists_logs_and_follow_ups(env):
    f = env.fake
    f.say(ROUTE_RESEARCH, usage=(50, 30))
    f.say(None, [("ratios", {"symbol": "TCS"}), ("ratios", {"symbol": "INFY"})])
    f.say(ANSWER)
    c = client()
    sid = c.post("/api/session").json()["session_id"]
    r = c.post(f"/api/session/{sid}/message", json={"message": "compare TCS with INFY"})
    assert r.status_code == 200, r.text
    body = r.json()
    msg = body["message"]
    assert "<follow_ups>" not in msg["content"]
    assert msg["follow_ups"] == ["What about HCL?", "Show TCS margins"]
    assert body["title"] == "TCS vs IT peers" and body["route"] == "research"
    assert [b["tool"] for b in msg["blocks"]] == ["ratios", "ratios"]
    assert msg["sources"][0]["source"] == "FakeSource"
    assert any(s["label"] == "Understood your question" for s in msg["steps"])

    # history round-trips
    detail = c.get(f"/api/session/{sid}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert c.get("/api/sessions").json()[0]["session_id"] == sid

    # --- LLM logs: 3 calls, all attributed to this session
    calls = c.get(f"/api/session/{sid}/llm-calls").json()
    assert [x["stage"] for x in calls] == ["route_turn", "research", "research"]
    assert [x["call_no"] for x in calls] == [1, 2, 3]
    assert calls[0]["usage_source"] == "provider" and calls[0]["total_tokens"] == 80
    assert calls[1]["tool_calls"][0]["name"] == "ratios"
    assert calls[1]["messages"][0]["role"] == "system"
    assert calls[2]["messages"][-1]["role"] == "tool"          # tool results were really sent back to the model
    usage = c.get(f"/api/session/{sid}/usage").json()
    assert usage["calls"] == 3 and usage["errors"] == 0

    log = c.get(f"/api/session/{sid}/log").text
    for needle in ("REQUEST START", "STAGE START  route_turn", "LLM CALL", "LLM RESPONSE", "TOOL CALL",
                   "compare TCS with INFY", "REQUEST END"):
        assert needle in log, needle
    logs = env.tmp / "logs"
    assert (logs / "index.log").exists()
    assert not (logs / "sessions" / "_unassigned.log").exists()  # nothing leaked outside a session


def test_second_turn_sends_history_to_model(env):
    f = env.fake
    c = client()
    sid = c.post("/api/session").json()["session_id"]
    f.say(ROUTE_RESEARCH); f.say("first answer")
    c.post(f"/api/session/{sid}/message", json={"message": "first q"})
    f.say(ROUTE_RESEARCH); f.say("second answer")
    c.post(f"/api/session/{sid}/message", json={"message": "and peers?"})
    research_req = f.requests[-1]["messages"]
    assert [m["role"] for m in research_req] == ["system", "user", "assistant", "user"]
    assert research_req[1]["content"] == "first q"
    # title is set once, on the first turn only
    assert c.get(f"/api/session/{sid}").json()["title"] == "TCS vs IT peers"


def test_out_of_scope_skips_research(env):
    f = env.fake
    f.say(json.dumps({"route": "out_of_scope", "topic": "", "title": "Pasta", "rationale": "x"}))
    c = client()
    sid = c.post("/api/session").json()["session_id"]
    r = c.post(f"/api/session/{sid}/message", json={"message": "best pasta recipe?"}).json()
    assert r["route"] == "out_of_scope" and len(f.requests) == 1
    assert len(r["message"]["follow_ups"]) == 3


def test_router_failure_falls_back_to_research(env):
    f = env.fake
    f.say(RuntimeError("gateway 500"))      # router blows up
    f.say("plain answer")
    c = client()
    sid = c.post("/api/session").json()["session_id"]
    r = c.post(f"/api/session/{sid}/message", json={"message": "hi tcs"}).json()
    assert r["route"] == "research" and r["message"]["content"] == "plain answer"
    calls = c.get(f"/api/session/{sid}/llm-calls").json()
    assert calls[0]["error"] and "gateway 500" in calls[0]["error"]     # failed call is logged too
    assert "ROUTER FALLBACK" in c.get(f"/api/session/{sid}/log").text


def test_llm_failure_is_recorded_and_excluded_from_next_prompt(env):
    f = env.fake
    env.settings.router_enabled = False
    c = client()
    sid = c.post("/api/session").json()["session_id"]
    f.say(RuntimeError("timeout"))
    m = c.post(f"/api/session/{sid}/message", json={"message": "q1"}).json()["message"]
    assert m["error"] and "try again" in m["content"]
    f.say("ok now")
    c.post(f"/api/session/{sid}/message", json={"message": "q2"})
    roles = [(x["role"], x["content"]) for x in f.requests[-1]["messages"][1:]]
    assert roles == [("user", "q1"), ("user", "q2")]       # the failed reply is not fed back to the model


def test_tool_budget_exhausted_forces_final_answer(env):
    f = env.fake
    env.settings.router_enabled = False
    env.settings.max_tool_rounds = 2
    for _ in range(2):
        f.say(None, [("ratios", {"symbol": "TCS"})])
    f.say("forced answer")
    c = client()
    sid = c.post("/api/session").json()["session_id"]
    m = c.post(f"/api/session/{sid}/message", json={"message": "q"}).json()["message"]
    assert m["content"] == "forced answer"
    assert "tools" not in f.requests[-1]           # last call had no tools


def test_api_edges(env):
    c = client()
    assert c.post("/api/session/nope/message", json={"message": "x"}).status_code == 404
    assert c.get("/api/session/nope/status").json() == {"busy": False}
    assert c.post("/api/session/nope/message", json={"message": ""}).status_code == 422
    sid = c.post("/api/session").json()["session_id"]
    env.fake.say(ROUTE_RESEARCH); env.fake.say("a")
    mid = c.post(f"/api/session/{sid}/message", json={"message": "q"}).json()["message"]["id"]
    assert c.post(f"/api/session/{sid}/feedback", json={"message_id": mid, "value": "up"}).status_code == 200
    assert c.get(f"/api/session/{sid}").json()["messages"][-1]["feedback"] == "up"
    assert c.patch(f"/api/session/{sid}", json={"title": "Renamed"}).json()["title"] == "Renamed"
    assert c.delete(f"/api/session/{sid}").status_code == 200
    assert c.get(f"/api/session/{sid}").status_code == 404


def test_split_follow_ups_variants():
    from backend.app.services.orchestration.nodes import split_follow_ups
    assert split_follow_ups("a") == ("a", [])
    t, f = split_follow_ups('body\n<follow_ups>\n["x y z", "p q r"]')      # unterminated tag
    assert t == "body" and f == ["x y z", "p q r"]
    t, f = split_follow_ups("body\n<follow_ups>- not json 'x'</follow_ups>")
    assert t == "body" and f == []
