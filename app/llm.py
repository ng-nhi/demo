"""Client gọi llama-server (API tương thích OpenAI: /v1/chat/completions).

LLM_MOCK=1 -> trả lời giả để dev giao diện khi chưa tải model.
"""
import asyncio
import json
import logging
import os
from typing import AsyncIterator

import httpx

log = logging.getLogger("uvicorn.error")

LLAMA_URL = os.getenv("LLAMA_URL", "http://127.0.0.1:8080")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
MOCK = os.getenv("LLM_MOCK", "0") == "1"
GATEWAY_CHAT = os.getenv("LLM_GATEWAY_MODE", "") == "chat"  # LLAMA_URL là gateway của Người 2 (POST /chat, SSE {"t": ...}) thay vì llama-server
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "256"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))
TIMEOUT = float(os.getenv("LLM_TIMEOUT", "120"))


class LLMUnavailable(Exception):
    pass


class LLMBusy(LLMUnavailable):
    """Gateway/llama-server trả 429: hàng đợi đầy. Web báo khách thử lại, không coi là AI hỏng."""


def _payload(messages, stream):
    p = {"messages": messages, "max_tokens": MAX_TOKENS, "temperature": TEMPERATURE, "stream": stream}
    if LLM_MODEL:
        p["model"] = LLM_MODEL
    return p


def _client():
    headers = {}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    return httpx.AsyncClient(base_url=LLAMA_URL, timeout=httpx.Timeout(TIMEOUT, connect=10), headers=headers)


async def health() -> bool:
    if MOCK:
        return True
    if LLM_API_KEY or LLAMA_URL.startswith("https://"):
        return True
    try:
        async with _client() as c:
            return (await c.get("/health")).status_code == 200
    except httpx.HTTPError:
        return False


MOCK_TEXT = ("[MOCK] Mình gợi ý Cà phê muối (35.000đ) béo mặn nhẹ, hoặc Nước ép dưa hấu (39.000đ) "
             "ít ngọt, mát. Bạn muốn gọi món nào?")


async def _gateway_stream(messages) -> AsyncIterator[str]:
    """Gọi gateway của Người 2: POST /chat {message, messages, ...} -> SSE {"t": "..."} ... [DONE] (hoặc {"error": "..."})."""
    body = {"message": messages[-1]["content"], "messages": messages, "max_tokens": MAX_TOKENS, "temperature": TEMPERATURE}
    try:
        async with _client() as c:
            async with c.stream("POST", "/chat", json=body) as r:
                if r.status_code == 429:
                    raise LLMBusy("Gateway trả 429 (hàng đợi đầy)")
                if r.status_code != 200:
                    raise LLMUnavailable(f"gateway trả về {r.status_code}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    obj = json.loads(data)
                    if "error" in obj:
                        raise LLMUnavailable(obj["error"])
                    if obj.get("t"):
                        yield obj["t"]
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
        msg = f"Không kết nối được gateway tại {LLAMA_URL} ({type(e).__name__})"
        log.warning("%s. Hãy bật gateway/llama-server đúng cổng, hoặc đặt LLM_MOCK=1.", msg)
        raise LLMUnavailable(msg) from e


async def chat_stream(messages) -> AsyncIterator[str]:
    """Phát từng đoạn text khi model sinh ra (dùng cho SSE)."""
    if MOCK:
        for w in MOCK_TEXT.split(" "):
            await asyncio.sleep(0.03)
            yield w + " "
        return
    if GATEWAY_CHAT:
        async for t in _gateway_stream(messages):
            yield t
        return
    try:
        async with _client() as c:
            async with c.stream("POST", "/v1/chat/completions", json=_payload(messages, True)) as r:
                if r.status_code == 429:
                    raise LLMBusy("LLM đang quá tải (429)")
                if r.status_code != 200:
                    raise LLMUnavailable(f"llama-server trả về {r.status_code}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
                    if delta:
                        yield delta
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
        msg = f"Không kết nối được llama-server tại {LLAMA_URL} ({type(e).__name__})"
        log.warning("%s. Hãy bật llama-server đúng cổng, hoặc đặt biến LLAMA_URL / LLM_MOCK=1.", msg)
        raise LLMUnavailable(msg) from e


async def chat(messages) -> str:
    return "".join([t async for t in chat_stream(messages)]).strip()
