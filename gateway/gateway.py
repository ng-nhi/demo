"""GATEWAY CỦA NGƯỜI 2 (bản gốc nguyên văn: benchmark/nguoi2/gateway_benchmark.py), đã sửa tối thiểu để web thật dùng được.

Giữ nguyên của Người 2: giao diện POST /chat + GET /health, biến môi trường LLAMA_URL / VERSION / MAX_QUEUE / PARALLEL / USE_CACHE / USE_STREAM,
semaphore PARALLEL, hàng chờ MAX_QUEUE (quá thì 429), cache Redis `cache:*`, thống kê `stat:hit|miss`, định dạng SSE {"t": "..."} + [DONE],
mặc định max_tokens=120 và temperature=0.

Mọi chỗ khác bản gốc đều có chú thích `# [SỬA]`, gồm:
  1. Nhận thêm trường `messages` (prompt hệ thống + lịch sử + danh sách món đã lọc do web dựng); không có thì chạy như bản gốc.
  2. Redis lấy từ REDIS_URL, không có Redis thì tự dùng RAM (bản gốc sập nếu Redis chưa bật).
  3. Cache chỉ cho câu hỏi 1 lượt, khóa gắn với prompt, bỏ qua câu trả lời sai tiền hoặc bị ngắt giữa chừng.
  4. Semaphore giữ suốt lúc stream (bản gốc nhả ngay khi trả StreamingResponse nên không giới hạn được gì), hàng chờ nhả đúng khi khách ngắt.
  5. 429 luôn bật (bản gốc chỉ bật khi tên VERSION có chữ "backpressure").
  6. /health kiểm tra llama-server thật; lỗi llama-server trả thành sự kiện lỗi rõ ràng; thêm /stats.

Chạy cùng web:  python start.py --gateway ...        Chạy riêng:  uvicorn gateway.gateway:app --port 8082
"""
import asyncio
import hashlib
import json
import os

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

try:
    import redis
except ImportError:  # [SỬA] không bắt buộc phải có thư viện redis
    redis = None

from app.cache_policy import is_cacheable, valid_amounts  # [SỬA] quy tắc cache chung với web
from app.menu import load_menu, norm

app = FastAPI()

LLAMA_URL = os.environ.get("LLAMA_URL", "http://127.0.0.1:8081")
VERSION = os.environ.get("VERSION", "V4")
MAX_QUEUE = int(os.environ.get("MAX_QUEUE", "50"))
PARALLEL = int(os.environ.get("PARALLEL", "4"))
USE_CACHE = os.environ.get("USE_CACHE", "1") == "1"
USE_STREAM = os.environ.get("USE_STREAM", "1") == "1"
BACKPRESSURE = os.environ.get("BACKPRESSURE", "1") == "1"  # [SỬA] 429 luôn bật
SYSTEM_PROMPT_PATH = os.environ.get("SYSTEM_PROMPT_PATH", "/content/system_prompt.txt")  # [SỬA] chỉ dùng khi web không gửi `messages`
TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "180"))
VALID = valid_amounts(load_menu())


class _Store:
    """[SỬA] Redis nếu REDIS_URL kết nối được (mặc định 127.0.0.1:6379), không thì RAM; Redis lỗi giữa chừng cũng chuyển sang RAM."""

    def __init__(self):
        self.r, self.mem = None, {}
        url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        if redis is not None and url:
            try:
                r = redis.Redis.from_url(url, decode_responses=True)
                r.ping()
                self.r = r
            except Exception:
                self.r = None

    def get(self, k):
        if self.r is not None:
            try:
                return self.r.get(k)
            except Exception:
                self.r = None
        return self.mem.get(k)

    def set(self, k, v):
        if self.r is not None:
            try:
                return self.r.set(k, v, ex=int(os.environ.get("CACHE_TTL", "3600")))
            except Exception:
                self.r = None
        self.mem[k] = v

    def incr(self, k):
        if self.r is not None:
            try:
                return self.r.incr(k)
            except Exception:
                self.r = None
        self.mem[k] = int(self.mem.get(k, 0)) + 1
        return self.mem[k]


rds = _Store()

# Semaphore kiểm soát số lượng request xử lý đồng thời tại Gateway
sem = asyncio.Semaphore(PARALLEL)
# Bộ đếm hàng chờ hiện tại
queue_counter = 0


def _client():  # [SỬA] tách ra để test thay được
    return httpx.AsyncClient(base_url=LLAMA_URL, timeout=httpx.Timeout(TIMEOUT, connect=5))


def _messages(body):
    """[SỬA] Ưu tiên `messages` do web gửi (đã có prompt + lịch sử + dữ liệu lọc); không có thì dựng như bản gốc."""
    msgs = body.get("messages")
    if msgs:
        return msgs
    system_prompt = ""
    if os.path.exists(SYSTEM_PROMPT_PATH):
        with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
            system_prompt = f.read()
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": body.get("message", "")}]


def _cache_key(msgs, message):
    """[SỬA] Khóa cache: câu 1 lượt (system + user) gắn với prompt và chuẩn hóa chữ; nhiều lượt thì không cache."""
    if not USE_CACHE:
        return None
    if len(msgs) == 2 and [m.get("role") for m in msgs] == ["system", "user"]:
        version = hashlib.sha256(str(msgs[0].get("content")).encode("utf-8")).hexdigest()[:10]
        return "cache:" + hashlib.sha256(f"{version}|{norm(str(msgs[1].get('content')))}".encode("utf-8")).hexdigest()
    return None


def _sse_text(text):
    return f"data: {json.dumps({'t': text})}\n\n"


@app.get("/health")
async def health():
    try:  # [SỬA] báo đúng tình trạng llama-server để web biết AI có sẵn sàng không
        async with _client() as c:
            ok = (await c.get("/health")).status_code == 200
    except httpx.HTTPError:
        ok = False
    return JSONResponse({"status": "ok" if ok else "llama-server chưa sẵn sàng"}, status_code=200 if ok else 503)


@app.get("/stats")
async def stats():  # [SỬA]
    hit, miss, rej = (int(rds.get(f"stat:{k}") or 0) for k in ("hit", "miss", "rejected"))
    return {"hit": hit, "miss": miss, "rejected": rej, "cache_hit_pct": round(100 * hit / (hit + miss), 1) if hit + miss else 0.0,
            "queue_waiting": queue_counter, "parallel": PARALLEL, "max_queue": MAX_QUEUE, "cache_enabled": USE_CACHE, "version": VERSION}


@app.post("/chat")
async def chat(request: Request):
    global queue_counter
    body = await request.json()
    message = body.get("message", "")
    msgs = _messages(body)  # [SỬA]
    key = _cache_key(msgs, message)  # [SỬA]

    # 1. Kiểm tra Cache
    if key:
        cached_res = rds.get(key)
        if cached_res:
            rds.incr("stat:hit")

            async def stream_cached():
                yield _sse_text(cached_res)
                if USE_STREAM:
                    yield "data: [DONE]\n\n"
            return StreamingResponse(stream_cached(), media_type="text/event-stream")
        rds.incr("stat:miss")

    # 2. Cơ chế Backpressure giới hạn hàng chờ
    if BACKPRESSURE or "backpressure" in VERSION or VERSION == "V3-backpressure":  # [SỬA] luôn bật
        if queue_counter >= MAX_QUEUE:
            rds.incr("stat:rejected")
            raise HTTPException(status_code=429, detail="Too Many Requests")

    llama_payload = {
        "model": "qwen2.5-3b",
        "messages": msgs,
        "temperature": body.get("temperature", 0),   # [SỬA] web có thể gửi giá trị riêng; mặc định như bản gốc
        "max_tokens": body.get("max_tokens", 120),
        "stream": USE_STREAM,
    }
    queue_counter += 1
    waiting = True  # [SỬA] để nhả đúng 1 lần dù chạy xong, lỗi, hay khách ngắt trước khi tới lượt

    def leave_queue():
        nonlocal waiting
        global queue_counter
        if waiting:
            waiting = False
            queue_counter -= 1

    if USE_STREAM:
        async def stream_generator():
            full_text, finished = "", False
            try:
                async with sem:  # [SỬA] giữ semaphore suốt lúc stream
                    leave_queue()
                    try:
                        async with _client() as client:
                            async with client.stream("POST", "/v1/chat/completions", json=llama_payload) as resp:
                                if resp.status_code != 200:
                                    yield f"data: {json.dumps({'error': f'llama-server trả về {resp.status_code}'})}\n\n"
                                    return
                                async for line in resp.aiter_lines():
                                    if not line:
                                        continue
                                    if line.startswith("data:"):
                                        data_str = line[5:].strip()
                                        if data_str == "[DONE]":
                                            finished = True
                                            yield "data: [DONE]\n\n"
                                            break
                                        try:
                                            chunk_json = json.loads(data_str)
                                            delta = chunk_json["choices"][0]["delta"].get("content", "")
                                            if delta:
                                                full_text += delta
                                                yield _sse_text(delta)
                                        except Exception:
                                            pass
                    except httpx.HTTPError as e:  # [SỬA] llama-server tắt/đứt: báo lỗi thay vì sập 500 giữa stream
                        yield f"data: {json.dumps({'error': 'llama-server không phản hồi (' + type(e).__name__ + ')'})}\n\n"
                        return
                text = full_text.strip()
                if key and finished and is_cacheable(text, VALID):  # [SỬA] không cache câu sai tiền / bị ngắt
                    rds.set(key, text)
            finally:
                leave_queue()
        return StreamingResponse(stream_generator(), media_type="text/event-stream")

    # Không stream
    try:
        async with sem:
            leave_queue()
            async with _client() as client:
                resp = await client.post("/v1/chat/completions", json=llama_payload)
        if resp.status_code != 200:  # [SỬA] báo đúng lỗi thay vì KeyError -> 500
            raise HTTPException(status_code=502, detail=f"llama-server trả về {resp.status_code}")
        full_text = resp.json()["choices"][0]["message"]["content"]
        if key and is_cacheable(full_text.strip(), VALID):
            rds.set(key, full_text.strip())

        async def stream_single():
            yield _sse_text(full_text)
        return StreamingResponse(stream_single(), media_type="text/event-stream")
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"llama-server không phản hồi ({type(e).__name__})")
    finally:
        leave_queue()
