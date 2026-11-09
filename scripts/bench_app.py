"""Đo tải web THẬT (/api/chat/stream) giống cách Người 2 đo gateway benchmark.

Khác biệt quan trọng: bộ câu hỏi của Người 2 ("Cho mình 2 trà đào cam sả size L, tổng bao nhiêu?"...)
trong app thật được tầng luật/FAQ trả lời ngay bằng code, KHÔNG chạm LLM. Muốn đo LLM + hàng đợi + cache
thì phải hỏi câu tư vấn mở. Script này mặc định dùng loại câu đó (--mode llm) và ghi lại nguồn trả lời
(llm / cache / faq / rules ...) từ sự kiện `done` của SSE để biết mỗi request đã đi đường nào.

Cách dùng (web đã chạy, ví dụ bằng cell 5 của notebook Colab hoặc `python start.py`):
    python scripts/bench_app.py --users 10 20 30 --duration 60 --label V4
    python scripts/bench_app.py --users 20 --mode mixed --label V4-mixed
Khi đổi cấu hình (V1..V4) hãy khởi động lại start.py với tham số tương ứng rồi chạy lại với --label khác:
    V3: --parallel 4 --no-cache      V4: --parallel 4 (có cache)      V2: --parallel 1 --no-cache
Kết quả: in bảng ra màn hình và ghi benchmark/ket_qua_app_<label>.csv (+ _chi_tiet.csv).
"""
import argparse
import asyncio
import csv
import json
import random
import statistics
import time
import uuid
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
ITEMS = [i["name"] for i in json.loads((ROOT / "menu.json").read_text(encoding="utf-8"))["items"]]
DRINKS = [n for n in ITEMS if n not in ("Tiramisu", "Tart trứng", "Su kem", "Macaron")]

OPEN_QUESTIONS = [  # câu tư vấn mở: đi vào LLM
    "Gợi ý cho mình một món nước ép không caffeine.",
    "Mình thích vị chua ngọt, nên uống gì?",
    "Trời nóng quá, có món nào mát không?",
    "Mình muốn món béo béo ngậy, gợi ý giúp mình",
    "Có món nào hợp để uống buổi tối không?",
    "Gợi ý món cho 2 người, một người dị ứng sữa",
    "Mình muốn thử món mới lạ",
    "Món nào ít ngọt mà thơm?",
    "Có món nào cho bé uống được không?",
    "Mình thích đồ chua, gợi ý trà nào?",
]
FAQ_QUESTIONS = [  # câu Người 2 dùng: app thật trả bằng code (nguồn faq/rules)
    "Quán có những món cà phê nào?", "Món nào bán chạy nhất?", "Có món nào không chứa sữa không?",
    "Size L phụ thu bao nhiêu?", "Bánh nào có chứa gluten?", "Trà nào có caffeine?",
    "Topping nào dùng được cho sinh tố?",
]


def make_questions(mode, n_unique=60, seed=0):
    rnd = random.Random(seed)
    qs = list(OPEN_QUESTIONS)
    while len(qs) < len(OPEN_QUESTIONS) + n_unique:
        a, b = rnd.sample(DRINKS, 2)
        qs.append(rnd.choice([f"Món nào ăn kèm {a} ngon?", f"{a} có ngọt không?",
                              f"So sánh {a} và {b} giúp mình", f"Mình đang phân vân giữa {a} và {b}, nên chọn gì?"]))
    qs = list(dict.fromkeys(qs))
    if mode == "mixed":
        qs += FAQ_QUESTIONS * 3
        for _ in range(20):
            qs.append(f"Cho mình {rnd.randint(1, 3)} {rnd.choice(ITEMS)}, tổng bao nhiêu tiền?")
    return qs


async def one_request(client, base, question):
    t0 = time.perf_counter()
    ttft, source, status = None, None, None
    try:
        async with client.stream("POST", f"{base}/api/chat/stream",
                                 json={"session_id": uuid.uuid4().hex[:12], "message": question}) as r:
            status = r.status_code
            if status == 200:
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    ev = json.loads(line[5:])
                    if ev.get("type") == "token" and ttft is None:
                        ttft = time.perf_counter() - t0
                    elif ev.get("type") == "done":
                        source = ev.get("source")
                    elif ev.get("type") == "error":
                        status = "ai_error"
    except httpx.TimeoutException:
        status = "timeout"
    except httpx.HTTPError:
        status = "error"
    t1 = time.perf_counter()
    return {"status": status, "source": source or "", "ttft": ttft, "total": t1 - t0, "t_start": t0, "t_end": t1}


async def run_load(base, users, duration, questions, timeout, think=(0.5, 1.5)):
    rows, stop_at = [], time.perf_counter() + duration
    limits = httpx.Limits(max_connections=users + 20, max_keepalive_connections=users + 20)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), limits=limits) as client:
        async def user(uid):
            await asyncio.sleep(random.uniform(0, 1))
            while time.perf_counter() < stop_at:
                rec = await one_request(client, base, random.choice(questions))
                rec["user"] = uid
                rows.append(rec)
                await asyncio.sleep(random.uniform(*think))
        await asyncio.gather(*[user(i) for i in range(users)])
    return rows


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return float("nan")
    return xs[min(len(xs) - 1, int(round(q / 100 * (len(xs) - 1))))]


def summarize(rows, label, users):
    n = len(rows)
    ok = [r for r in rows if r["status"] == 200]
    elapsed = max(max(r["t_end"] for r in rows) - min(r["t_start"] for r in rows), 1e-9)
    src = Counter(r["source"] for r in ok)
    llm_ok = [r for r in ok if r["source"] == "llm"]
    return {
        "label": label, "users": users, "requests": n, "ok": len(ok), "rps": round(len(ok) / elapsed, 2),
        "ttft_p50_ms": round(pct([r["ttft"] for r in ok], 50) * 1000), "ttft_p95_ms": round(pct([r["ttft"] for r in ok], 95) * 1000),
        "lat_p95_s": round(pct([r["total"] for r in ok], 95), 2),
        "llm_ttft_p50_ms": round(pct([r["ttft"] for r in llm_ok], 50) * 1000) if llm_ok else "",
        "llm_ttft_p95_ms": round(pct([r["ttft"] for r in llm_ok], 95) * 1000) if llm_ok else "",
        "dat_duoi_3s_pct": round(100 * sum(1 for r in ok if r["ttft"] is not None and r["ttft"] < 3) / n, 1),
        "reject429_pct": round(100 * sum(1 for r in rows if r["status"] == 429) / n, 1),
        "error_pct": round(100 * sum(1 for r in rows if r["status"] not in (200, 429)) / n, 1),
        "src_llm": src.get("llm", 0), "src_cache": src.get("cache", 0),
        "src_code": sum(v for k, v in src.items() if k not in ("llm", "cache")),
    }


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--users", type=int, nargs="+", default=[10, 20, 30])
    ap.add_argument("--duration", type=int, default=60, help="giây cho mỗi mức người dùng")
    ap.add_argument("--mode", choices=["llm", "mixed"], default="llm", help="llm: chỉ câu tư vấn mở; mixed: trộn thêm câu FAQ/tính tiền")
    ap.add_argument("--label", default="app", help="tên cấu hình (V1..V4) để ghi vào file kết quả")
    ap.add_argument("--timeout", type=float, default=60.0)
    a = ap.parse_args()

    async with httpx.AsyncClient(timeout=10) as c:
        h = (await c.get(f"{a.url}/api/health")).json()
        print("Health:", h)
        if h.get("mock") or not h.get("llm"):
            print("CẢNH BÁO: đang chạy mock hoặc llm=false, số đo sẽ không phản ánh model thật.")
    questions = make_questions(a.mode)
    print(f"{len(questions)} câu hỏi khác nhau, chế độ {a.mode}")

    out_dir = ROOT / "benchmark"
    out_dir.mkdir(exist_ok=True)
    summary, detail = [], []
    for u in a.users:
        print(f"\n{u} người dùng, {a.duration}s ...", flush=True)
        async with httpx.AsyncClient(timeout=10) as c:
            before = (await c.get(f"{a.url}/api/stats")).json()
        rows = await run_load(a.url, u, a.duration, questions, a.timeout)
        async with httpx.AsyncClient(timeout=10) as c:
            after = (await c.get(f"{a.url}/api/stats")).json()
        s = summarize(rows, a.label, u)
        s["cache_hit_pct_stats"] = round(100 * (after["hit"] - before["hit"]) / max(1, (after["hit"] - before["hit"]) + (after["miss"] - before["miss"])), 1)
        summary.append(s)
        detail += [{**r, "label": a.label, "users": u} for r in rows]
        print({k: s[k] for k in ("rps", "ttft_p50_ms", "ttft_p95_ms", "llm_ttft_p50_ms", "dat_duoi_3s_pct", "reject429_pct", "error_pct", "src_llm", "src_cache", "src_code")})
        await asyncio.sleep(3)

    with open(out_dir / f"ket_qua_app_{a.label}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0])); w.writeheader(); w.writerows(summary)
    with open(out_dir / f"ket_qua_app_{a.label}_chi_tiet.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(detail[0])); w.writeheader(); w.writerows(detail)
    print(f"\nĐã ghi benchmark/ket_qua_app_{a.label}.csv")


if __name__ == "__main__":
    asyncio.run(main())
