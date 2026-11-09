"""Giỏ hàng theo phiên (in-memory) + chốt đơn + hiểu lệnh đặt món bằng luật (rule-based).

Vì sao dùng luật thay vì nhờ model 3B tự xuất JSON? Model nhỏ hay sai số lượng/giá;
luật cho kết quả chắc chắn, nhanh (không tốn lượt suy luận LLM) và dễ test.
"""
import json
import re
import threading
import time
from pathlib import Path

from .menu import fmt_vnd, norm, price_line

NUM_WORDS = {"mot": 1, "hai": 2, "ba": 3, "bon": 4, "nam": 5, "sau": 6, "bay": 7, "tam": 8, "chin": 9, "muoi": 10}
ORDERS_FILE = Path(__file__).resolve().parent.parent / "data" / "orders.jsonl"

EXTRA_ALIASES = {  # tên gọi tắt khách hay dùng (đã bỏ dấu)
    "tra_dao_cam_sa": ["tra dao"], "ep_dua_hau": ["dua hau"], "ca_phe_cot_dua": ["cot dua"],
    "sinh_to_viet_quat": ["viet quat"], "tra_xoai_chanh_day": ["tra xoai"], "nau_da": ["nau"],
}
TOPPING_ALIASES = {"tran_chau": ["tran chau"], "kem_cheese": ["cheese"], "shot_espresso": ["shot espresso", "them shot"]}


class Cart:
    def __init__(self):
        self.lines = []  # mỗi dòng: {item_id,size,toppings,qty}

    def add(self, menu, item_id, size="M", toppings=(), qty=1):
        item = menu["_items"].get(item_id)
        if item is None:
            raise ValueError("Món không tồn tại")
        size = size if item["sized"] else "M"
        toppings = sorted(toppings)
        price_line(menu, item_id, size, toppings, qty)  # validate, lỗi -> ValueError
        for ln in self.lines:
            if (ln["item_id"], ln["size"], ln["toppings"]) == (item_id, size, toppings):
                ln["qty"] += qty
                return
        self.lines.append({"item_id": item_id, "size": size, "toppings": toppings, "qty": qty})

    def set_qty(self, idx, qty):
        if not 0 <= idx < len(self.lines):
            raise ValueError("Dòng không tồn tại")
        if qty <= 0:
            self.lines.pop(idx)
        else:
            self.lines[idx]["qty"] = min(qty, 50)

    def remove_item(self, item_id):
        before = len(self.lines)
        self.lines = [l for l in self.lines if l["item_id"] != item_id]
        return before - len(self.lines)

    def view(self, menu):
        out, total = [], 0
        for i, ln in enumerate(self.lines):
            item = menu["_items"][ln["item_id"]]
            sub = price_line(menu, ln["item_id"], ln["size"], ln["toppings"], ln["qty"])
            total += sub
            out.append({
                "index": i, "item_id": ln["item_id"], "name": item["name"],
                "size": ln["size"] if item["sized"] else None,
                "toppings": [menu["_toppings"][t]["name"] for t in ln["toppings"]],
                "qty": ln["qty"], "subtotal": sub,
            })
        return {"lines": out, "total": total, "total_text": fmt_vnd(total), "count": sum(l["qty"] for l in self.lines)}


class Store:
    """Kho phiên in-memory. Người 2 có thể thay bằng Redis khi chạy nhiều worker."""

    def __init__(self):
        self.carts, self.history = {}, {}
        self.order_seq = self._count_orders()  # nối tiếp mã đơn sau khi restart, không quay về DH0001
        self.lock = threading.Lock()

    @staticmethod
    def _count_orders():
        try:
            with open(ORDERS_FILE, encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except FileNotFoundError:
            return 0

    def cart(self, sid):
        return self.carts.setdefault(sid, Cart())

    def hist(self, sid):
        return self.history.setdefault(sid, [])

    def checkout(self, menu, sid):
        cart = self.cart(sid)
        if not cart.lines:
            raise ValueError("Giỏ hàng đang trống")
        with self.lock:
            self.order_seq += 1
            order = {"order_id": f"DH{self.order_seq:04d}", "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                     **cart.view(menu)}
            ORDERS_FILE.parent.mkdir(exist_ok=True)
            with open(ORDERS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(order, ensure_ascii=False) + "\n")
        self.carts[sid] = Cart()
        return order


def cart_text(view):
    if not view["lines"]:
        return "Giỏ hàng của bạn đang trống."
    rows = []
    for l in view["lines"]:
        extra = (f" size {l['size']}" if l["size"] else "") + (", " + ", ".join(l["toppings"]) if l["toppings"] else "")
        rows.append(f"- {l['qty']} x {l['name']}{extra}: {fmt_vnd(l['subtotal'])}")
    return "Giỏ hàng hiện tại:\n" + "\n".join(rows) + f"\nTổng cộng: {view['total_text']}"


# ---------- Hiểu câu lệnh ----------
def _find_items(menu, t):
    """Tìm các món trong câu đã chuẩn hóa. Khớp tên dài trước để 'ep dua hau' không bị 'ep dua' cướp."""
    cands = []
    for it in menu["items"]:
        for name in [norm(it["name"])] + EXTRA_ALIASES.get(it["id"], []):
            cands.append((name, it["id"]))
    cands.sort(key=lambda x: -len(x[0]))
    masked, found = t, []
    for name, iid in cands:
        for m in re.finditer(rf"\b{re.escape(name)}\b", masked):
            found.append((m.start(), m.end(), iid))
            masked = masked[:m.start()] + "#" * (m.end() - m.start()) + masked[m.end():]
    return sorted(found)


def _find_toppings(menu, seg):
    ids = []
    for tp in menu["toppings"]:
        names = [norm(tp["name"]).replace("them ", "")] + TOPPING_ALIASES.get(tp["id"], [])
        if any(re.search(rf"\b{re.escape(n)}\b", seg) for n in names):
            ids.append(tp["id"])
    return ids


def _qty(before, after):
    m = re.match(r"\s*x\s*(\d+)\b|\s*(\d+)\s*(?:ly|cai|phan|chiec|suat)\b", after)
    if m:
        return int(m.group(1) or m.group(2))
    m = re.search(r"(\d+|" + "|".join(NUM_WORDS) + r")\s*(?:ly|cai|phan|chiec|suat)?\s*$", before)
    if m:
        v = m.group(1)
        return int(v) if v.isdigit() else NUM_WORDS[v]
    return 1


def parse_order(menu, text):
    """Trả về list dòng [{item_id,size,toppings,qty}] hoặc []."""
    t = norm(text)
    found = _find_items(menu, t)
    lines, prev_end = [], 0
    for i, (s, e, iid) in enumerate(found):
        nxt = found[i + 1][0] if i + 1 < len(found) else len(t)
        seg_after = t[e:nxt]
        size = "L" if re.search(r"\bsize l\b|\bco l\b|\blon\b|\bl\b", seg_after) else "M"
        lines.append({"item_id": iid, "size": size, "toppings": _find_toppings(menu, seg_after),
                      "qty": min(_qty(t[prev_end:s], seg_after), 50)})
        prev_end = e
    return lines


# Nhận diện ý định: dùng ranh giới từ (\b) để "giao" không bị tính là "gia", "lay" không dính chữ khác.
QUESTION_RE = re.compile(r"\?|\b(hoi|bao nhieu|gia|goi y|tu van|nen|mon nao|co gi|khac gi)\b")
ORDER_VERB_RE = re.compile(r"\b(cho minh|cho toi|lay|dat|order|goi|mua|muon|them)\b")
_QTY_BEFORE_RE = re.compile(r"(\d+|" + "|".join(NUM_WORDS) + r")\s*(?:ly|cai|phan|chiec|suat)?\s*$")
_SIZE_RE = re.compile(r"\bsize [lm]\b")


def _has_order_intent(menu, t):
    """Có động từ đặt món, hoặc có số lượng ngay trước tên món ('5 nau da', 'hai ly nau da'),
    hoặc có chỉ định size ('ca phe muoi size L')."""
    if ORDER_VERB_RE.search(t) or _SIZE_RE.search(t):
        return True
    for s, _e, _iid in _find_items(menu, t):
        if _QTY_BEFORE_RE.search(t[:s]):
            return True
    return False


def handle_cart_command(menu, store, sid, text):
    """Trả về chuỗi trả lời nếu là lệnh giỏ hàng; None nếu nên chuyển cho LLM tư vấn."""
    t = norm(text)
    cart = store.cart(sid)

    if re.search(r"\b(chot don|thanh toan|xac nhan don|dat hang|chot)\b", t):
        try:
            order = store.checkout(menu, sid)
        except ValueError as e:
            return f"Chưa chốt được: {e}. Bạn muốn gọi món gì nào?"
        return (f"Đã chốt đơn {order['order_id']}!\n" + cart_text(order).split("\n", 1)[1]
                + "\nCảm ơn bạn, quán sẽ chuẩn bị ngay.")
    if re.search(r"\b(xoa het|huy don|lam lai|xoa gio)\b", t):
        store.carts[sid] = Cart()
        return "Đã xóa toàn bộ giỏ hàng."
    if re.search(r"\b(xem gio|gio hang|tinh tien|tong tien|tong cong|het bao nhieu)\b", t) and not _find_items(menu, t):
        return cart_text(cart.view(menu))

    found = _find_items(menu, t)
    if not found:
        return None
    # Kiểm tra động từ "bỏ/xóa/hủy" trên câu đã che tên món: "sinh tố bơ" (norm -> "sinh to bo") KHÔNG phải lệnh bỏ món
    t_verbs = t
    for s_, e_, _ in found:
        t_verbs = t_verbs[:s_] + " " * (e_ - s_) + t_verbs[e_:]
    if re.search(r"\b(bo|xoa|huy|khong lay)\b", t_verbs):
        names = []
        for _, _, iid in found:
            if cart.remove_item(iid):
                names.append(menu["_items"][iid]["name"])
        return (f"Đã bỏ {', '.join(names)} khỏi giỏ.\n" + cart_text(cart.view(menu))) if names else "Món đó chưa có trong giỏ."

    is_question = bool(QUESTION_RE.search(t)) and "vao gio" not in t
    if is_question or not _has_order_intent(menu, t):
        return None  # hỏi đáp -> LLM
    added = []
    try:
        for ln in parse_order(menu, text):
            cart.add(menu, ln["item_id"], ln["size"], ln["toppings"], ln["qty"])
            added.append(f"{ln['qty']} x {menu['_items'][ln['item_id']]['name']}")
    except ValueError as e:
        return f"Không thêm được: {e}."
    return f"Đã thêm {', '.join(added)} vào giỏ.\n" + cart_text(cart.view(menu)) + "\nBạn gõ 'chốt đơn' để đặt nhé."
