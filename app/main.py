"""FastAPI gateway: web chat + giỏ hàng + gọi llama-server.  Chạy: uvicorn app.main:app --port 8000"""
import asyncio
import hashlib
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from . import faq, llm
from .cache_policy import is_cacheable, valid_amounts
from .cart import Store, cart_text, handle_cart_command
from .menu import build_general_prompt, build_system_prompt, fmt_vnd, load_menu
from .shared import Shared

BASE = Path(__file__).resolve().parent.parent
MENU = load_menu()
SYSTEM_PROMPT = build_system_prompt(MENU)
GENERAL_PROMPT = build_general_prompt(MENU)  # câu ngoài menu: trả lời đúng câu hỏi, không bịa
STORE = Store()
HISTORY_TURNS = 6  # chỉ giữ 6 tin gần nhất để prompt không phình

# --- Hàng đợi / 429 / cache (phối hợp với Người 2) ---
# Độ sâu hàng đợi, cache và thống kê nằm trong `shared`: có REDIS_URL thì dùng Redis (nhiều worker dùng chung),
# không có thì dùng bộ nhớ trong tiến trình. Khóa Redis giống gateway benchmark của Người 2.
LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "1"))   # số request chạy LLM cùng lúc (khớp --parallel của llama-server)
MAX_QUEUE = int(os.getenv("MAX_QUEUE", "50"))              # quá LLM_CONCURRENCY + MAX_QUEUE request đang chờ/chạy -> 429
USE_CACHE = os.getenv("USE_CACHE", "1") == "1"             # cache câu trả lời LLM (chỉ câu độc lập, không chứa số tiền lạ)
CACHE_TTL = int(os.getenv("CACHE_TTL", "3600"))
_slots = asyncio.Semaphore(LLM_CONCURRENCY)
shared = Shared(os.getenv("REDIS_URL"))

PROMPT_VERSION = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:10]  # đổi menu/prompt -> cache cũ vô hiệu
GENERAL_VERSION = hashlib.sha256(GENERAL_PROMPT.encode("utf-8")).hexdigest()[:10]

# Quy tắc cache nằm ở app/cache_policy.py (gateway dùng chung): câu trả lời chứa số tiền KHÁC giá thật trong menu không được cache.
VALID_AMOUNTS = valid_amounts(MENU)
BUSY_MSG = "Quán đang rất đông, bạn vui lòng thử lại sau ít giây nhé."

# Khách chỉ thấy câu này; chi tiết kỹ thuật (địa chỉ, loại lỗi) nằm trong log của server và /api/health
AI_DOWN_MSG = ("Trợ lý AI tạm thời chưa sẵn sàng. Bạn vẫn đặt món được bằng menu bên dưới "
               "hoặc gõ ví dụ “cho mình 2 cà phê muối size L”.")

app = FastAPI(title="Cafe AI Chat")


class ChatIn(BaseModel):
    session_id: str = Field(min_length=4, max_length=64)
    message: str = Field(min_length=1, max_length=500)


class AddIn(BaseModel):
    session_id: str
    item_id: str
    size: str = "M"
    toppings: list[str] = []
    qty: int = Field(1, ge=1, le=50)


class QtyIn(BaseModel):
    session_id: str
    index: int
    qty: int


class SessionIn(BaseModel):
    session_id: str


def _too_busy():
    return JSONResponse(
        status_code=429, headers={"Retry-After": "5"},
        content={"detail": BUSY_MSG})


def _remember(sid, user, bot):
    h = STORE.hist(sid)
    h += [{"role": "user", "content": user}, {"role": "assistant", "content": bot}]
    del h[:-HISTORY_TURNS * 2]


def _cacheable(reply):
    return is_cacheable(reply, VALID_AMOUNTS)


def _route(sid, text):
    """Quyết định cách trả lời một tin nhắn:
      1) lệnh giỏ hàng (luật)           -> {"mode": "reply", source "rules"}
      2) tra cứu/chào hỏi/thông tin quán (code) -> {"mode": "reply", source "faq"|"chitchat"|"info"}
      3) còn lại (tư vấn mở + câu ngoài lề)     -> {"mode": "llm", messages, cache_key}
    """
    rule_reply = handle_cart_command(MENU, STORE, sid, text)
    if rule_reply is not None:
        return {"mode": "reply", "reply": rule_reply, "source": "rules", "remember": False}
    res = faq.answer(MENU, text, has_history=bool(STORE.hist(sid)))
    if res.reply is not None:
        return {"mode": "reply", "reply": res.reply, "source": res.kind, "remember": res.remember}
    if res.general:  # ngoài lề: prompt tổng quát, không kèm menu (ngắn, nhanh, không bị menu kéo lệch); câu độc lập nên cache được
        key = shared.cache_key(GENERAL_VERSION, text) if (USE_CACHE and not llm.MOCK) else None
        return {"mode": "llm", "messages": [{"role": "system", "content": GENERAL_PROMPT}, {"role": "user", "content": text}], "cache_key": key}
    # Câu độc lập thì KHÔNG gửi lịch sử (prompt ngắn, nhanh, không bị câu trả lời cũ làm lệch, và cache được).
    hist = STORE.hist(sid)[-HISTORY_TURNS:] if res.followup else []
    content = text if not res.hint else f"{text}\n\n(Dữ liệu đã lọc từ hệ thống: {res.hint})"
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *hist, {"role": "user", "content": content}]
    key = shared.cache_key(PROMPT_VERSION, text) if (USE_CACHE and not llm.MOCK and not res.followup) else None
    return {"mode": "llm", "messages": messages, "cache_key": key}


async def _admit():
    """Xếp vào hàng đợi LLM. False nếu quá tải (-> 429)."""
    depth = await shared.depth_incr()
    if depth > LLM_CONCURRENCY + MAX_QUEUE:
        await shared.depth_decr()
        await shared.stat_incr("rejected")
        return False
    return True


@app.on_event("startup")
async def _startup():
    await shared.reset()


@app.get("/api/health")
async def health():
    return {"web": "ok", "llm": await llm.health(), "mock": llm.MOCK, "llm_url": llm.LLAMA_URL, "inflight": await shared.depth()}


@app.get("/api/stats")
async def stats():
    """Số liệu cho báo cáo/benchmark: cache hit/miss, số request bị 429, độ sâu hàng đợi, đang dùng Redis hay RAM."""
    return {**await shared.stats(), "cache_enabled": USE_CACHE and not llm.MOCK, "parallel": LLM_CONCURRENCY, "max_queue": MAX_QUEUE}


@app.get("/api/menu")
async def get_menu():
    best = set(MENU.get("bestsellers_seed", []))  # giao diện dùng cờ này để hiện nhãn "Bán chạy"
    items = [{**i, "price_text": fmt_vnd(i["price"]), "bestsellers": i["id"] in best} for i in MENU["items"]]
    return {"shop_name": MENU.get("shop_name", "Quán Cà Phê"), "categories": MENU["categories"], "sizes": MENU["sizes"], "items": items, "toppings": MENU["toppings"]}


@app.get("/api/cart/{session_id}")
async def get_cart(session_id: str):
    return STORE.cart(session_id).view(MENU)


@app.post("/api/cart/add")
async def cart_add(b: AddIn):
    try:
        STORE.cart(b.session_id).add(MENU, b.item_id, b.size, b.toppings, b.qty)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return STORE.cart(b.session_id).view(MENU)


@app.post("/api/cart/qty")
async def cart_qty(b: QtyIn):
    try:
        STORE.cart(b.session_id).set_qty(b.index, b.qty)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return STORE.cart(b.session_id).view(MENU)


@app.post("/api/checkout")
async def checkout(b: SessionIn):
    try:
        return STORE.checkout(MENU, b.session_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/chat")
async def chat(b: ChatIn):
    """Không streaming: trả JSON một lần."""
    d = _route(b.session_id, b.message)
    cart = lambda: STORE.cart(b.session_id).view(MENU)  # noqa: E731
    if d["mode"] == "reply":
        if d["remember"]:
            _remember(b.session_id, b.message, d["reply"])
        return {"reply": d["reply"], "source": d["source"], "cart": cart()}

    key = d["cache_key"]
    if key:
        hit = await shared.cache_get(key)
        if hit is not None:
            await shared.stat_incr("hit")
            _remember(b.session_id, b.message, hit)
            return {"reply": hit, "source": "cache", "cart": cart()}
        await shared.stat_incr("miss")

    if not await _admit():
        return _too_busy()
    try:
        async with _slots:
            reply = await llm.chat(d["messages"])
    except llm.LLMBusy:
        return _too_busy()
    except llm.LLMUnavailable:
        raise HTTPException(503, AI_DOWN_MSG)
    finally:
        await shared.depth_decr()
    if key and _cacheable(reply):
        await shared.cache_set(key, reply, CACHE_TTL)
    _remember(b.session_id, b.message, reply)
    return {"reply": reply, "source": "llm", "cart": cart()}


def _sse(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@app.post("/api/chat/stream")
async def chat_stream(b: ChatIn):
    """SSE: các sự kiện {type:token|done|error}. Giao diện hiện chữ ngay khi model sinh ra."""
    sid = b.session_id
    d = _route(sid, b.message)

    if d["mode"] == "reply":
        async def one():
            yield _sse({"type": "token", "text": d["reply"]})
            if d["remember"]:
                _remember(sid, b.message, d["reply"])
            yield _sse({"type": "done", "source": d["source"], "cart": STORE.cart(sid).view(MENU)})
        return StreamingResponse(one(), media_type="text/event-stream", headers=SSE_HEADERS)

    key = d["cache_key"]
    if key:
        hit = await shared.cache_get(key)
        if hit is not None:
            await shared.stat_incr("hit")

            async def cached():
                for i in range(0, len(hit), 24):  # trả theo từng đoạn để giao diện vẫn "gõ chữ" như khi chạy LLM
                    yield _sse({"type": "token", "text": hit[i:i + 24]})
                _remember(sid, b.message, hit)
                yield _sse({"type": "done", "source": "cache", "cart": STORE.cart(sid).view(MENU)})
            return StreamingResponse(cached(), media_type="text/event-stream", headers=SSE_HEADERS)
        await shared.stat_incr("miss")

    if not await _admit():  # đăng ký TRƯỚC khi trả về để 429 trả được bằng status code thật
        return _too_busy()

    released = False

    async def release():  # chỉ nhả 1 lần dù gọi từ generator hay từ background task
        nonlocal released
        if not released:
            released = True
            await shared.depth_decr()

    async def gen():
        parts = []
        try:
            async with _slots:
                async for tok in llm.chat_stream(d["messages"]):
                    parts.append(tok)
                    yield _sse({"type": "token", "text": tok})
            text = "".join(parts).strip()
            if key and _cacheable(text):
                await shared.cache_set(key, text, CACHE_TTL)
            _remember(sid, b.message, text)
            yield _sse({"type": "done", "source": "llm", "cart": STORE.cart(sid).view(MENU)})
        except llm.LLMBusy:
            yield _sse({"type": "error", "message": BUSY_MSG, "busy": True})
        except llm.LLMUnavailable:
            yield _sse({"type": "error", "message": AI_DOWN_MSG})
        finally:
            await release()

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS, background=BackgroundTask(release))


@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html")


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
