import os
import time
import json
import asyncio
import httpx
import redis
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse

app = FastAPI()

LLAMA_URL = os.environ.get("LLAMA_URL", "http://127.0.0.1:8081")
VERSION = os.environ.get("VERSION", "V4")
MAX_QUEUE = int(os.environ.get("MAX_QUEUE", "50"))
PARALLEL = int(os.environ.get("PARALLEL", "4"))
USE_CACHE = os.environ.get("USE_CACHE", "1") == "1"
USE_STREAM = os.environ.get("USE_STREAM", "1") == "1"

# Kết nối Redis
rds = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)

# Semaphore kiểm soát số lượng request xử lý đồng thời tại Gateway
sem = asyncio.Semaphore(PARALLEL)
# Bộ đếm hàng chờ hiện tại
queue_counter = 0

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/chat")
async def chat(request: Request):
    global queue_counter
    body = await request.json()
    message = body.get("message", "")

    # 1. Kiểm tra Cache
    if USE_CACHE:
        cached_res = rds.get(f"cache:{message}")
        if cached_res:
            rds.incr("stat:hit")
            if USE_STREAM:
                async def stream_cached():
                    yield f"data: {json.dumps({'t': cached_res})}\n\n"
                    yield "data: [DONE]\n\n"
                return StreamingResponse(stream_cached(), media_type="text/event-stream")
            else:
                async def stream_raw():
                    yield f"data: {json.dumps({'t': cached_res})}\n\n"
                return StreamingResponse(stream_raw(), media_type="text/event-stream")
        else:
            rds.incr("stat:miss")

    # 2. Cơ chế Backpressure giới hạn hàng chờ
    if "backpressure" in VERSION or VERSION == "V3-backpressure":
        if queue_counter >= MAX_QUEUE:
            raise HTTPException(status_code=429, detail="Too Many Requests")

    queue_counter += 1
    try:
        async with sem:
            queue_counter -= 1
            
            # Đọc SYSTEM_PROMPT
            system_prompt = ""
            if os.path.exists("/content/system_prompt.txt"):
                with open("/content/system_prompt.txt", "r", encoding="utf-8") as f:
                    system_prompt = f.read()

            # Payload gửi tới Llama-server
            llama_payload = {
                "model": "qwen2.5-3b",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message}
                ],
                "temperature": 0,
                "max_tokens": 120,
                "stream": USE_STREAM
            }

            client = httpx.AsyncClient(timeout=180.0)
            
            if USE_STREAM:
                async def stream_generator():
                    full_text = ""
                    try:
                        async with client.stream("POST", f"{LLAMA_URL}/v1/chat/completions", json=llama_payload) as resp:
                            async for line in resp.aiter_lines():
                                if not line:
                                    continue
                                if line.startswith("data:"):
                                    data_str = line[5:].strip()
                                    if data_str == "[DONE]":
                                        yield "data: [DONE]\n\n"
                                        break
                                    try:
                                        chunk_json = json.loads(data_str)
                                        delta = chunk_json["choices"][0]["delta"].get("content", "")
                                        if delta:
                                            full_text += delta
                                            yield f"data: {json.dumps({'t': delta})}\n\n"
                                    except Exception:
                                        pass
                        if USE_CACHE and full_text:
                            rds.set(f"cache:{message}", full_text)
                    finally:
                        await client.aclose()
                return StreamingResponse(stream_generator(), media_type="text/event-stream")
            else:
                # Không stream
                resp = await client.post(f"{LLAMA_URL}/v1/chat/completions", json=llama_payload)
                await client.aclose()
                res_json = resp.json()
                full_text = res_json["choices"][0]["message"]["content"]
                if USE_CACHE and full_text:
                    rds.set(f"cache:{message}", full_text)
                
                async def stream_single():
                    yield f"data: {json.dumps({'t': full_text})}\n\n"
                return StreamingResponse(stream_single(), media_type="text/event-stream")
                
    except Exception as e:
        if queue_counter > 0:
            queue_counter -= 1
        raise HTTPException(status_code=500, detail=str(e))
