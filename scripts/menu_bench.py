"""
Menu helpers + đo tốc độ 1 request với mô hình local (llama.cpp / GGUF).

Cài đặt:
    pip install llama-cpp-python

Chạy kiểm tra menu (không cần mô hình):
    python menu_bench.py --check

Đo tốc độ 1 request:
    python menu_bench.py --model ./models/qwen2.5-1.5b-instruct-q4_k_m.gguf
    python menu_bench.py --model ./models/xxx.gguf --runs 3 --threads 4 --gpu-layers 0
"""
import argparse
import json
import time
from pathlib import Path

MENU_PATH = Path(__file__).resolve().parent.parent / "menu.json"


# ---------- Menu ----------
def load_menu(path=MENU_PATH):
    with open(path, encoding="utf-8") as f:
        menu = json.load(f)
    menu["_items"] = {i["id"]: i for i in menu["items"]}
    menu["_toppings"] = {t["id"]: t for t in menu["toppings"]}
    return menu


def fmt_vnd(n):
    return f"{n:,}".replace(",", ".") + "đ"


def price_line(menu, item_id, size="M", topping_ids=(), qty=1):
    """Backend tự tính tiền, AI không được tự tính. Ném ValueError nếu dữ liệu sai."""
    item = menu["_items"].get(item_id)
    if item is None:
        raise ValueError(f"Món không tồn tại: {item_id}")
    if qty < 1:
        raise ValueError("Số lượng phải >= 1")

    unit = item["price"]
    if item["sized"]:
        if size not in menu["sizes"]:
            raise ValueError(f"Size không hợp lệ: {size}")
        unit += menu["sizes"][size]
    for tid in topping_ids:
        top = menu["_toppings"].get(tid)
        if top is None:
            raise ValueError(f"Topping không tồn tại: {tid}")
        if item["category"] not in top["categories"]:
            raise ValueError(f"{top['name']} không dùng được cho {item['name']}")
        unit += top["price"]
    return unit * qty


def menu_to_prompt(menu):
    """Menu dạng text gọn để nhét vào system prompt (ít token)."""
    lines = []
    for cat_id, cat_name in menu["categories"].items():
        lines.append(f"[{cat_name}]")
        for it in menu["items"]:
            if it["category"] != cat_id:
                continue
            size_note = f" (size L +{fmt_vnd(menu['sizes']['L'])})" if it["sized"] else ""
            lines.append(f"- {it['id']}: {it['name']} {fmt_vnd(it['price'])}{size_note}")
        tops = [t for t in menu["toppings"] if cat_id in t["categories"]]
        if tops:
            lines.append("  Topping: " + ", ".join(f"{t['name']} +{fmt_vnd(t['price'])}" for t in tops))
    return "\n".join(lines)


def build_system_prompt(menu, bestsellers=()):
    best = ", ".join(menu["_items"][i]["name"] for i in bestsellers if i in menu["_items"])
    best_line = f"Món bán chạy: {best}\n" if best else ""
    return (
        "Bạn là nhân viên tư vấn của quán cà phê, nói tiếng Việt, thân thiện, ngắn gọn.\n"
        "Chỉ nói về món có trong menu dưới đây. Không tự bịa món, giá hay topping.\n"
        "Nếu khách hỏi ngoài phạm vi quán, từ chối lịch sự và gợi ý quay lại menu.\n\n"
        f"MENU:\n{menu_to_prompt(menu)}\n\n{best_line}"
    )


# ---------- Đo tốc độ ----------
def benchmark(model_path, runs, threads, gpu_layers, max_tokens):
    from llama_cpp import Llama

    t0 = time.perf_counter()
    llm = Llama(
        model_path=model_path,
        n_ctx=2048,
        n_threads=threads,
        n_gpu_layers=gpu_layers,
        verbose=False,
    )
    load_s = time.perf_counter() - t0
    print(f"Thời gian load mô hình: {load_s:.2f}s")

    menu = load_menu()
    messages = [
        {"role": "system", "content": build_system_prompt(menu, ["ca_phe_muoi", "tra_dao_cam_sa"])},
        {"role": "user", "content": "Quán có món nào ít ngọt, giá dưới 40k không? Tư vấn giúp mình."},
    ]

    results = []
    for r in range(1, runs + 1):
        start = time.perf_counter()
        first = None
        n_tokens = 0
        text = []
        for chunk in llm.create_chat_completion(
            messages=messages, max_tokens=max_tokens, temperature=0.3, stream=True
        ):
            delta = chunk["choices"][0]["delta"].get("content")
            if delta:
                if first is None:
                    first = time.perf_counter() - start
                n_tokens += 1  # xấp xỉ: mỗi chunk ~ 1 token
                text.append(delta)
        total = time.perf_counter() - start
        gen_time = max(total - (first or 0), 1e-9)
        tps = n_tokens / gen_time
        results.append((first or total, total, n_tokens, tps))
        print(f"\n--- Lần {r} ---")
        print("".join(text))
        print(f"TTFT: {first:.2f}s | Tổng: {total:.2f}s | Token: {n_tokens} | {tps:.1f} token/s")

    avg = [sum(x[i] for x in results) / len(results) for i in range(4)]
    print(
        f"\nTRUNG BÌNH ({runs} lần): TTFT {avg[0]:.2f}s | Tổng {avg[1]:.2f}s | "
        f"{avg[2]:.0f} token | {avg[3]:.1f} token/s"
    )
    size_mb = Path(model_path).stat().st_size / 1024 / 1024
    print(f"Kích thước file mô hình: {size_mb:.0f} MB")


def check_menu():
    menu = load_menu()
    print(f"Số món: {len(menu['items'])}, số topping: {len(menu['toppings'])}")
    for cat_id, cat_name in menu["categories"].items():
        n = sum(1 for i in menu["items"] if i["category"] == cat_id)
        print(f"  {cat_name}: {n} món")
    print("\nVí dụ tính tiền:")
    print("  Cà phê muối size L + kem cheese x2 =",
          fmt_vnd(price_line(menu, "ca_phe_muoi", "L", ["kem_cheese"], 2)))
    print("  Tiramisu + kem tươi =", fmt_vnd(price_line(menu, "tiramisu", topping_ids=["kem_tuoi"])))
    try:
        price_line(menu, "nau_da", "M", ["thach_dao"])
    except ValueError as e:
        print("  Lỗi hợp lệ (đúng như mong đợi):", e)
    print("\nPrompt menu (đếm sơ bộ):", len(build_system_prompt(menu).split()), "từ")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="kiểm tra menu và hàm tính tiền")
    ap.add_argument("--model", help="đường dẫn file .gguf")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--gpu-layers", type=int, default=0)
    ap.add_argument("--max-tokens", type=int, default=150)
    args = ap.parse_args()

    if args.check or not args.model:
        check_menu()
    if args.model:
        benchmark(args.model, args.runs, args.threads, args.gpu_layers, args.max_tokens)
