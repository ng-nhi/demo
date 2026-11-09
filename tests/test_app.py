"""Chạy: pytest -q   (không cần model: dùng LLM_MOCK=1)"""
import os

os.environ["LLM_MOCK"] = "1"

import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

import app.cart as cart_mod  # noqa: E402

# Test chốt đơn ghi vào file tạm, KHÔNG làm bẩn data/orders.jsonl thật (phải đặt trước khi nạp app.main)
cart_mod.ORDERS_FILE = Path(tempfile.mkdtemp()) / "orders.jsonl"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

c = TestClient(app)
SID = "test-session-1"


def chat(msg, sid=SID):
    r = c.post("/api/chat", json={"session_id": sid, "message": msg})
    assert r.status_code == 200, r.text
    return r.json()


def test_menu_has_20_to_30_items():
    assert 20 <= len(c.get("/api/menu").json()["items"]) <= 30


def test_price_is_computed_by_backend():
    r = chat("cho mình 2 cà phê muối size L thêm kem cheese", "s-price")
    assert r["source"] == "rules"
    assert r["cart"]["total"] == (35000 + 10000 + 12000) * 2  # 114.000


def test_question_goes_to_llm():
    assert chat("cho mình hỏi món nào ít ngọt?", "s-question")["source"] == "llm"


def test_invalid_topping_rejected():
    assert "không dùng được" in chat("nâu đá thêm thạch đào", "s-bad")["reply"]


def test_remove_and_checkout():
    sid = "s-co"
    chat("1 tiramisu và 2 su kem", sid)
    chat("bỏ su kem", sid)
    r = chat("chốt đơn", sid)
    assert "DH" in r["reply"] and r["cart"]["total"] == 0
    assert "trống" in chat("chốt đơn", sid)["reply"]


def test_cart_api():
    sid = "s-api"
    c.post("/api/cart/add", json={"session_id": sid, "item_id": "ep_cam", "size": "L"})
    v = c.get(f"/api/cart/{sid}").json()
    assert v["total"] == 52000
    assert c.post("/api/cart/add", json={"session_id": sid, "item_id": "khong_co"}).status_code == 400
    c.post("/api/cart/qty", json={"session_id": sid, "index": 0, "qty": 0})
    assert c.get(f"/api/cart/{sid}").json()["lines"] == []


def test_stream_sse():
    with c.stream("POST", "/api/chat/stream", json={"session_id": "s-sse", "message": "gợi ý món mát"}) as r:
        body = "".join(r.iter_text())
    assert '"type": "token"' in body and '"type": "done"' in body


def test_429_when_queue_full(monkeypatch):
    import app.main as m
    monkeypatch.setattr(m.shared, "_mem_depth", m.LLM_CONCURRENCY + m.MAX_QUEUE)  # hàng đợi đã đầy
    r = c.post("/api/chat", json={"session_id": "s-429", "message": "gợi ý món mát"})
    assert r.status_code == 429 and r.headers["retry-after"]


def test_prompt_has_allergen_and_caffeine_info():
    from app.main import SYSTEM_PROMPT
    assert "chứa sữa" in SYSTEM_PROMPT and "không caffeine" in SYSTEM_PROMPT


def test_menu_endpoint_has_shop_name():
    assert c.get("/api/menu").json()["shop_name"] == "Hallelujah Coffee"


def test_order_without_verb_still_goes_to_rules():
    # số lượng >3, số bằng chữ, hoặc chỉ có size -> vẫn là lệnh đặt món, không đẩy sang LLM
    for i, msg in enumerate(["5 nâu đá", "hai ly nâu đá", "cà phê muối size L"]):
        r = chat(msg, f"s-noverb-{i}")
        assert r["source"] == "rules" and r["cart"]["count"] >= 1, msg


def test_giao_is_not_a_price_question():
    # "giao" không được khớp nhầm với "gia" (giá)
    r = chat("lấy 1 cà phê muối giao tận nơi", "s-giao")
    assert r["source"] == "rules" and r["cart"]["total"] == 35000


def test_price_question_is_answered_by_code_not_llm():
    # tra giá là việc của code (chính xác 100%); model 3B chỉ đúng khoảng 7/10 câu loại này
    r = chat("nâu đá giá bao nhiêu", "s-price-q")
    assert r["source"] == "faq" and "29.000đ" in r["reply"] and "39.000đ" in r["reply"]


def test_menu_marks_bestsellers():
    items = {i["id"]: i for i in c.get("/api/menu").json()["items"]}
    assert items["ca_phe_muoi"]["bestsellers"] is True
    assert items["nau_da"]["bestsellers"] is False


def test_llm_down_shows_friendly_message_and_health(monkeypatch):
    """llama-server không chạy -> khách thấy câu thân thiện (không lộ ConnectError); /api/health báo rõ địa chỉ."""
    from app import llm

    monkeypatch.setattr(llm, "MOCK", False)
    monkeypatch.setattr(llm, "LLAMA_URL", "http://127.0.0.1:9")  # cổng không có ai lắng nghe

    r = c.post("/api/chat", json={"session_id": "s-down", "message": "gợi ý món cho buổi sáng"})
    assert r.status_code == 503
    assert "chưa sẵn sàng" in r.json()["detail"] and "ConnectError" not in r.json()["detail"]

    s = c.post("/api/chat/stream", json={"session_id": "s-down", "message": "gợi ý món cho buổi sáng"})
    assert "error" in s.text and "chưa sẵn sàng" in s.text and "ConnectError" not in s.text

    h = c.get("/api/health").json()
    assert h["llm"] is False and h["llm_url"] == "http://127.0.0.1:9"


def test_sinh_to_bo_is_an_item_not_a_remove_command():
    # "bơ" khi bỏ dấu thành "bo" (giống "bỏ"): đặt Sinh tố bơ phải thêm vào giỏ, còn "bỏ sinh tố bơ" mới là xóa
    r = chat("cho mình 1 sinh tố bơ size L", "s-bo")
    assert r["source"] == "rules" and r["cart"]["count"] == 1 and "Đã thêm" in r["reply"]
    chat("bỏ sinh tố bơ", "s-bo")
    assert c.get("/api/cart/s-bo").json()["count"] == 0


def test_total_question_is_computed_by_code():
    r = chat("Cho mình 2 trà đào cam sả size L, tổng bao nhiêu?", "s-total")
    assert r["source"] == "faq" and "110.000đ" in r["reply"] and r["cart"]["count"] == 0  # chỉ tính, chưa thêm vào giỏ


def test_off_topic_is_answered_by_general_prompt_not_refused(monkeypatch):
    from app import llm
    import app.main as m

    seen = []

    async def fake_stream(messages):
        seen.append(messages)
        yield "Mình không có công cụ tra giá vàng theo thời gian thực, bạn xem trên trang SJC nhé."

    monkeypatch.setattr(llm, "MOCK", False)
    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    r = chat("Hôm nay giá vàng bao nhiêu?", "s-off")
    assert r["source"] == "llm" and "giá vàng" in r["reply"]
    assert seen[0][0]["content"] == m.GENERAL_PROMPT and len(seen[0]) == 2  # prompt tổng quát, không kèm menu/lịch sử
    # thông tin quán chưa có trong dữ liệu thì vẫn nói thật bằng code, không gọi LLM
    assert chat("quán mở cửa lúc mấy giờ?", "s-off")["source"] == "info"


def test_caffeine_filter_is_computed_by_code():
    r = chat("món nào không caffeine?", "s-caf")
    assert r["source"] == "faq" and "Nước ép dưa hấu" in r["reply"] and "Cà phê muối" not in r["reply"]


def test_llm_answer_is_cached_for_independent_questions(monkeypatch):
    from app import llm
    import app.main as m

    calls = {"n": 0}

    async def fake_stream(messages):
        calls["n"] += 1
        yield "Bạn thử Nước ép dưa hấu (39.000đ), ít đường nhé."

    monkeypatch.setattr(llm, "MOCK", False)
    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    q = "gợi ý món cho buổi chiều mát mẻ"
    assert chat(q, "s-cache-1")["source"] == "llm"
    assert chat(q + " ", "s-cache-2")["source"] == "cache" and calls["n"] == 1
    st = c.get("/api/stats").json()
    assert st["hit"] >= 1 and st["miss"] >= 1 and st["backend"] in ("memory", "redis")
    # câu trả lời chứa số tiền không có trong menu (model tự cộng sai) thì không được cache
    assert m._cacheable("Tổng là 90.000đ.") is False and m._cacheable("Cà phê muối 35.000đ.") is True

