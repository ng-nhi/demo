"""Quy tắc "câu trả lời LLM nào được phép cache", dùng chung cho web (app/main.py) và gateway (app/gateway.py).

Model 3B hay tự cộng sai tiền, nên câu trả lời chứa số tiền KHÔNG có thật trong menu sẽ không bao giờ được cache.
"""
import re

_AMOUNT_RE = re.compile(r"\d{1,3}(?:\.\d{3})+|\d+\s*k\b", re.I)


def valid_amounts(menu):
    """Các số tiền hợp lệ: giá món, giá món + size L, giá topping, phụ thu size."""
    return ({i["price"] + extra for i in menu["items"] for extra in (0, *menu["sizes"].values())}
            | {t["price"] for t in menu["toppings"]} | set(menu["sizes"].values()))


def is_cacheable(reply, valid):
    """Có nội dung, không dài bất thường, và mọi số tiền trong đó đều là giá có thật."""
    if not reply or len(reply) > 1200:
        return False
    for m in _AMOUNT_RE.findall(reply):
        if m.lower().endswith("k"):
            return False
        if int(m.replace(".", "")) not in valid:
            return False
    return True
