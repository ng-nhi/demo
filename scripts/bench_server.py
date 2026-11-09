"""Đo tốc độ 1 request (hoặc tuần tự N lần) qua HTTP tới llama-server đang chạy.
Không cần llama-cpp-python -> dùng được trên Colab.

    python scripts/bench_server.py --runs 3
    python scripts/bench_server.py --url http://127.0.0.1:8080 --max-tokens 150
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.menu import build_system_prompt, load_menu  # noqa: E402

QUESTION = "Quán có món nào ít ngọt, giá dưới 40k không? Tư vấn giúp mình."


def one_run(client, messages, max_tokens):
    start, first, chunks, timings, text = time.perf_counter(), None, 0, {}, []
    body = {"messages": messages, "max_tokens": max_tokens, "temperature": 0.3, "stream": True}
    with client.stream("POST", "/v1/chat/completions", json=body) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data:") or line.strip() == "data: [DONE]":
                continue
            obj = json.loads(line[5:])
            timings = obj.get("timings") or timings  # llama-server gửi số liệu thật ở chunk cuối (nếu có)
            delta = (obj.get("choices") or [{}])[0].get("delta", {}).get("content")
            if delta:
                first = first or time.perf_counter() - start
                chunks += 1
                text.append(delta)
    total = time.perf_counter() - start
    first = first or total
    tps = timings.get("predicted_per_second") or chunks / max(total - first, 1e-9)
    return first, total, timings.get("predicted_n") or chunks, tps, "".join(text), timings.get("prompt_n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=150)
    a = ap.parse_args()
    menu = load_menu()
    msgs = [{"role": "system", "content": build_system_prompt(menu)}, {"role": "user", "content": QUESTION}]
    res = []
    with httpx.Client(base_url=a.url, timeout=300) as c:
        for i in range(1, a.runs + 1):
            f, t, n, tps, text, pn = one_run(c, msgs, a.max_tokens)
            res.append((f, t, n, tps))
            print(f"--- Lần {i} ---\n{text}\nTTFT {f:.2f}s | Tổng {t:.2f}s | {n} token | {tps:.1f} token/s"
                  + (f" | prompt {pn} token" if pn else ""))
    avg = [sum(r[k] for r in res) / len(res) for k in range(4)]
    print(f"\nTRUNG BÌNH {a.runs} lần: TTFT {avg[0]:.2f}s | Tổng {avg[1]:.2f}s | {avg[2]:.0f} token | {avg[3]:.1f} token/s")
    print("Lưu ý: lần 1 thường chậm hơn (khởi động); số token là xấp xỉ nếu server không trả 'timings'.")
