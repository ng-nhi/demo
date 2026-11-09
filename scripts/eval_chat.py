#!/usr/bin/env python3
"""Chấm chatbot bằng bộ câu hỏi tests/eval_questions.json.

    python scripts/eval_chat.py --offline           # chỉ kiểm tra phần trả lời bằng code (không cần server/model)
    python scripts/eval_chat.py --url http://127.0.0.1:8000    # chạy thật qua API (cần server + llama-server)

Mỗi câu có: source mong đợi (faq/info/chitchat/llm), must (phải có), must_not (không được có), any_of (có ít nhất 1).
Thêm câu mới vào eval_questions.json mỗi khi phát hiện chatbot trả lời sai: đó là cách cải thiện bền nhất.
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def check(case, reply, source):
    errs = []
    if case.get("source") and source != case["source"]:
        errs.append(f"source={source}, mong đợi {case['source']}")
    errs += [f"thiếu “{s}”" for s in case.get("must", []) if s not in reply]
    errs += [f"không được có “{s}”" for s in case.get("must_not", []) if s in reply]
    if case.get("any_of") and not any(s in reply for s in case["any_of"]):
        errs.append("không nhắc món nào trong " + str(case["any_of"]))
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--offline", action="store_true", help="chỉ chạy lớp faq (bỏ qua câu cần LLM)")
    ap.add_argument("--file", default=str(ROOT / "tests" / "eval_questions.json"))
    a = ap.parse_args()
    cases = json.load(open(a.file, encoding="utf-8"))

    if a.offline:
        from app import faq
        from app.menu import load_menu
        menu = load_menu()

        def ask(q):
            r = faq.answer(menu, q, False)
            return (r.reply, r.kind) if r.reply is not None else (None, "llm")
    else:
        import httpx
        client = httpx.Client(base_url=a.url, timeout=120)

        def ask(q):
            d = client.post("/api/chat", json={"session_id": "eval-" + uuid.uuid4().hex[:8], "message": q}).json()
            return d.get("reply") or d.get("detail", ""), d.get("source", "?")

    ok = bad = skipped = 0
    for case in cases:
        reply, source = ask(case["q"])
        if reply is None:  # offline và câu cần LLM
            if case.get("source") == "llm":
                skipped += 1
                continue
            errs = [f"mong đợi trả lời bằng code ({case.get('source')}) nhưng bị chuyển sang LLM"]
        else:
            errs = check(case, reply, source)
        if errs:
            bad += 1
            print(f"FAIL  {case['q']}\n      " + "; ".join(errs) + (f"\n      trả lời: {reply[:160]!r}" if reply else ""))
        else:
            ok += 1
            print(f"ok    {case['q']}")
    print(f"\n{ok} đạt, {bad} lỗi" + (f", {skipped} câu cần LLM (bỏ qua ở chế độ --offline)" if skipped else ""))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
