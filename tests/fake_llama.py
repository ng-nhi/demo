"""llama-server giả (cổng 8081) để test đường streaming mà không cần model.
Chạy: uvicorn tests.fake_llama:app --port 8081"""
import asyncio
import json

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def completions(body: dict):
    async def gen():
        for w in ["Xin ", "chào, ", "mình ", "gợi ", "ý ", "Nước ép dưa hấu."]:
            await asyncio.sleep(0.05)
            yield "data: " + json.dumps({"choices": [{"delta": {"content": w}}]}) + "\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
