"""Test gateway của Người 2 (gateway/gateway.py) bằng llama-server giả chạy trong cùng tiến trình. pytest -q tests/test_gateway.py"""
import asyncio
import json
import os

os.environ["LLM_MOCK"] = "0"

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import llm  # noqa: E402
from gateway import gateway as gw  # noqa: E402

STATE = {"calls": 0, "last": None, "active": 0, "peak": 0}


def make_upstream(text="Bạn thử Nước ép dưa hấu 39.000đ nhé.", status=200, truncate=False, delay=0.0):
    up = FastAPI()

    @up.get("/health")
    async def health():
        return {"status": "ok"}

    @up.post("/v1/chat/completions")
    async def comp(body: dict):
        STATE["calls"] += 1
        STATE["last"] = body
        if status != 200:
            return StreamingResponse(iter([b"{}"]), status_code=status)

        async def gen():
            STATE["active"] += 1
            STATE["peak"] = max(STATE["peak"], STATE["active"])
            try:
                for i, w in enumerate(text.split(" ")):
                    if delay:
                        await asyncio.sleep(delay)
                    if truncate and i == 2:
                        return  # ngắt giữa chừng, không có [DONE]
                    yield "data: " + json.dumps({"choices": [{"delta": {"content": w + " "}}]}) + "\n\n"
                yield "data: [DONE]\n\n"
            finally:
                STATE["active"] -= 1
        return StreamingResponse(gen(), media_type="text/event-stream")
    return up


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setattr(gw, "rds", gw._Store())  # RAM, không cần Redis
    monkeypatch.setattr(gw, "USE_CACHE", True)
    monkeypatch.setattr(gw, "USE_STREAM", True)
    monkeypatch.setattr(gw, "queue_counter", 0)
    STATE.update(calls=0, last=None, active=0, peak=0)

    def setup(**kw):
        up = make_upstream(**kw)
        monkeypatch.setattr(gw, "_client", lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=up), base_url="http://up"))
    setup()
    c = TestClient(gw.app)
    c.setup, c.mp = setup, monkeypatch
    return c


def body(user="gợi ý món mát", system="SYS"):
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return {"message": user, "messages": msgs}


def read_sse(resp):
    out = ""
    for line in resp.text.splitlines():
        if line.startswith("data:") and "[DONE]" not in line:
            d = json.loads(line[5:])
            out += d.get("t", "")
    return out.strip()


def test_stream_uses_web_messages_and_caches(client):
    r1 = client.post("/chat", json=body())
    assert r1.status_code == 200 and "[DONE]" in r1.text
    text = read_sse(r1)
    assert "Nước ép dưa hấu" in text and STATE["calls"] == 1
    assert STATE["last"]["messages"][0]["content"] == "SYS"  # prompt do web gửi, không phải file /content/system_prompt.txt
    assert STATE["last"]["max_tokens"] == 120 and STATE["last"]["temperature"] == 0  # mặc định của Người 2
    r2 = client.post("/chat", json=body(user="  Gợi ý món MÁT "))  # khác hoa/thường, khoảng trắng -> vẫn trúng cache
    assert read_sse(r2) == text and STATE["calls"] == 1
    s = client.get("/stats").json()
    assert s["hit"] == 1 and s["miss"] == 1 and s["queue_waiting"] == 0


def test_web_can_override_max_tokens_and_temperature(client):
    b = body()
    b.update(max_tokens=256, temperature=0.2)
    client.post("/chat", json=b)
    assert STATE["last"]["max_tokens"] == 256 and STATE["last"]["temperature"] == 0.2


def test_original_message_only_request_still_works(client, tmp_path):
    p = tmp_path / "sp.txt"
    p.write_text("PROMPT TỪ FILE", encoding="utf-8")
    client.mp.setattr(gw, "SYSTEM_PROMPT_PATH", str(p))
    r = client.post("/chat", json={"message": "xin chào"})  # đúng kiểu gọi của bản gốc
    assert r.status_code == 200 and "[DONE]" in r.text
    assert STATE["last"]["messages"] == [{"role": "system", "content": "PROMPT TỪ FILE"}, {"role": "user", "content": "xin chào"}]


def test_different_system_prompt_is_not_a_hit(client):
    client.post("/chat", json=body(system="A"))
    client.post("/chat", json=body(system="B"))  # đổi prompt/menu -> cache cũ vô hiệu
    assert STATE["calls"] == 2


def test_multi_turn_is_not_cached(client):
    b = body()
    b["messages"].insert(1, {"role": "assistant", "content": "xin chào"})
    client.post("/chat", json=b)
    client.post("/chat", json=b)
    assert STATE["calls"] == 2


def test_wrong_amount_and_truncated_answers_are_not_cached(client):
    client.setup(text="Tổng cộng là 123.456đ nhé.")
    client.post("/chat", json=body("tính tiền giúp"))
    client.post("/chat", json=body("tính tiền giúp"))
    assert STATE["calls"] == 2
    client.setup(text="Mình gợi ý Nước ép dưa hấu rất mát đó", truncate=True)
    client.post("/chat", json=body("câu bị ngắt"))
    client.post("/chat", json=body("câu bị ngắt"))
    assert STATE["calls"] == 4


def test_returns_429_when_queue_full(client):
    client.mp.setattr(gw, "MAX_QUEUE", 0)
    r = client.post("/chat", json=body("câu mới"))
    assert r.status_code == 429 and STATE["calls"] == 0
    assert client.get("/stats").json()["rejected"] == 1


def test_429_is_on_even_for_v4_version(client):  # bản gốc chỉ chặn khi VERSION có chữ "backpressure"
    client.mp.setattr(gw, "VERSION", "V4")
    client.mp.setattr(gw, "MAX_QUEUE", 0)
    assert client.post("/chat", json=body("v4")).status_code == 429


def test_upstream_error_becomes_error_event_and_queue_is_released(client):
    client.setup(status=503)
    r = client.post("/chat", json=body("lỗi"))
    assert r.status_code == 200 and '"error"' in r.text
    assert client.get("/stats").json()["queue_waiting"] == 0
    client.setup()
    assert "Nước ép" in read_sse(client.post("/chat", json=body("ổn lại")))


def test_health_checks_llama_server(client):
    assert client.get("/health").status_code == 200

    def boom():
        raise httpx.ConnectError("down")
    client.mp.setattr(gw, "_client", boom)
    assert client.get("/health").status_code == 503


def test_semaphore_really_limits_concurrent_streams(monkeypatch):
    """Bản gốc nhả semaphore ngay khi trả StreamingResponse nên PARALLEL không giới hạn được gì khi stream."""
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setattr(gw, "rds", gw._Store())
    monkeypatch.setattr(gw, "USE_CACHE", False)
    monkeypatch.setattr(gw, "USE_STREAM", True)
    monkeypatch.setattr(gw, "queue_counter", 0)
    STATE.update(calls=0, last=None, active=0, peak=0)
    up = make_upstream(text="a b c d e f", delay=0.03)
    monkeypatch.setattr(gw, "_client", lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=up), base_url="http://up"))

    async def run():
        monkeypatch.setattr(gw, "sem", asyncio.Semaphore(2))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app), base_url="http://gw") as c:
            rs = await asyncio.gather(*[c.post("/chat", json=body(f"câu {i}")) for i in range(6)])
        assert all(r.status_code == 200 and "[DONE]" in r.text for r in rs)
    asyncio.run(run())
    assert STATE["calls"] == 6 and STATE["peak"] <= 2


# ---------------- phía web: app/llm.py nói chuyện với gateway qua /chat
def _gateway_llm(monkeypatch, handler):
    monkeypatch.setattr(llm, "MOCK", False)
    monkeypatch.setattr(llm, "GATEWAY_CHAT", True)
    monkeypatch.setattr(llm, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gw"))


def _collect(messages=None):
    async def run():
        return [t async for t in llm.chat_stream(messages or [{"role": "system", "content": "S"}, {"role": "user", "content": "x"}])]
    return asyncio.run(run())


def test_llm_gateway_mode_sends_messages_and_parses_t(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = request.url.path, json.loads(request.content)
        return httpx.Response(200, content=b'data: {"t": "Xin "}\n\ndata: {"t": "chao"}\n\ndata: [DONE]\n\n')
    _gateway_llm(monkeypatch, handler)
    assert "".join(_collect()) == "Xin chao"
    assert seen["url"] == "/chat" and seen["body"]["message"] == "x" and len(seen["body"]["messages"]) == 2


def test_llm_gateway_mode_maps_429_and_error(monkeypatch):
    _gateway_llm(monkeypatch, lambda r: httpx.Response(429, json={"detail": "Too Many Requests"}))
    with pytest.raises(llm.LLMBusy):
        _collect()
    _gateway_llm(monkeypatch, lambda r: httpx.Response(200, content='data: {"error": "llama-server trả về 503"}\n\n'.encode("utf-8")))
    with pytest.raises(llm.LLMUnavailable):
        _collect()
