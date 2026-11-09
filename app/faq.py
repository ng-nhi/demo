"""Lớp trả lời bằng CODE đứng trước LLM: chính xác, tức thì, không bịa.

Model 3B hay sai khi tra cứu (giá, dị ứng, caffeine, lọc theo giá...) và hay "chiều" khách khi bị hỏi
ngoài lề. Vì vậy mọi thứ trả lời được bằng dữ liệu menu thì để code làm; LLM chỉ còn lo phần tư vấn mở
("gợi ý món mát", "món nào hợp buổi sáng"...), và khi đó được nhận thêm danh sách món đã lọc đúng.

    answer(menu, text, has_history) -> Result(reply, kind, remember, hint, followup)
        reply is None  -> chuyển sang LLM (hint = danh sách đã lọc, nếu có; followup = có dùng lịch sử chat)
        kind: "faq" (tra cứu) | "chitchat" | "info" (thông tin quán) | "offtopic" (không còn dùng: câu ngoài lề nay đi qua LLM, general=True)
"""
import re
from collections import namedtuple

from .cart import _find_items, _find_toppings, _has_order_intent, parse_order
from .menu import fmt_vnd, norm, price_line

Result = namedtuple("Result", "reply kind remember hint followup general", defaults=(False,))


def _llm(hint=None, followup=False, general=False):
    """general=True: câu ngoài menu -> LLM trả lời bằng prompt tổng quát (không bị ép chỉ nói về menu)."""
    return Result(None, "llm", False, hint, followup, general)


def _reply(text, kind="faq", remember=True):
    return Result(text, kind, remember, None, False)


# ---------------------------------------------------------------- nhận diện từ khóa (đã bỏ dấu)
GREET = r"(xin )?(chao|hello|hi|hey|alo)( ban| quan| shop| nhe| a| ad| minh| buoi sang| buoi toi)*"
THANKS = r"(cam on|thanks|thank you|tks|ok|oke|okie|da biet|duoc roi|tuyet|tot qua)( ban| nhe| nha| a| nhieu| quan| ban oi)*"
BYE = r"(tam biet|bye|goodbye|hen gap lai)( ban| nhe| nha| a)*"
OPENER = r"(cho minh hoi|minh hoi|hoi chut|ban oi|shop oi|ad oi|oi|a lo)( ban| chut| nhe| a| nha)*"
WHO_RE = re.compile(r"\b(ban la ai|ban ten gi|ten ban la gi|ban la bot|bot la gi|ai tao ra ban|ban la nguoi that)\b")
HOWRU_RE = re.compile(r"\b(ban khoe khong|ban co khoe khong|khoe khong|dang lam gi|an com chua)\b")
# Khách kể hoàn cảnh ("trời nóng quá", "mua cho bạn gái") = đang xin gợi ý món, không phải câu ngoài lề.
STATE_RE = re.compile(r"\b(nong|oi buc|nang gat|ret|met|met moi|stress|thuc khuya|tang ca|on bai)\b")
SOCIAL_RE = re.compile(r"\b(ban gai|ban trai|nguoi yeu|sep|dong nghiep|sinh nhat|hen ho|bo me|ba me|ban be|khach)\b")
ASKPICK_RE = re.compile(r"\b(chon|mua|goi y|uong gi|an gi|tang|qua|order gi|dat gi)\b")
HELP_RE = re.compile(r"\b(ban lam duoc gi|ban giup duoc gi|giup gi|huong dan|cach dat|dat mon nhu the nao|dat the nao|lam sao de dat)\b")

SHOP_INFO = [  # (nhãn hiển thị, regex) - dữ liệu này KHÔNG có trong menu.json nên không được bịa
    ("giờ mở cửa", r"gio mo cua|mo cua|dong cua|may gio"),
    ("địa chỉ / chỗ giữ xe", r"dia chi|o dau|dia diem|do xe|giu xe|parking"),
    ("số điện thoại / liên hệ", r"so dien thoai|sdt|hotline|lien he"),
    ("wifi", r"wifi|wi fi|mat khau"),
    ("giao hàng", r"giao hang|ship|freeship|van chuyen|delivery"),
    ("khuyến mãi", r"khuyen mai|giam gia|voucher|uu dai|combo|tich diem|thanh vien"),
    ("hình thức thanh toán", r"chuyen khoan|momo|zalopay|the tin dung|qr"),
    ("đặt bàn / chỗ ngồi", r"dat ban|cho ngoi|ngoai troi|dieu hoa|may lanh|phong rieng"),
    ("mang đi / dùng tại quán", r"mang di|mang ve|take ?away|an tai quan"),
    ("quy định thú cưng", r"thu cung|cho meo|pet"),
    ("chính sách đổi trả / hoàn tiền", r"doi tra|hoan tien|chinh sach|bao hanh|hoa don"),
    ("calo / dinh dưỡng", r"calo|kcal|dinh duong|duong huyet"),
]

PRICE_RE = re.compile(r"\b(gia|bao nhieu|nhieu tien|bao tien|het bao nhieu|gia ca|tinh tien|tong)\b")
TOTAL_RE = re.compile(r"\b(tong|het bao nhieu|thanh tien|tinh giup|tinh gia|tinh xem|cong lai)\b")
SUBJECTIVE_RE = re.compile(r"\b(ngon|nen|goi y|hop|thich|tot hon|hay hon|so sanh|khac gi|nao hon)\b")
ATTR_RE = re.compile(r"\b(thanh phan|co gi|nguyen lieu|lam tu|vi gi|vi nhu the nao|nhu the nao|mo ta|la gi|la mon gi)\b")
TOPPING_RE = re.compile(r"\b(topping|top)\b")
SIZE_RE = re.compile(r"\b(size|phu thu|co l|co m)\b")
BEST_RE = re.compile(r"\b(ban chay|best ?seller|noi bat|signature|dac trung|pho bien|nhieu nguoi (goi|uong|mua)|goi nhieu|hot nhat|yeu thich nhat|de xuat)\b")
OVERVIEW_RE = re.compile(r"\b(menu|thuc don|nhom mon|nhung nhom|quan ban gi|ban gi|co gi|co mon gi|mon gi|co nhung mon|co nhung nhom)\b")
LIST_ALL_RE = re.compile(r"\b(liet ke|xem het|xem toan bo|toan bo|tat ca cac mon|tat ca mon|tat ca thuc don|het menu|het thuc don)\b")
TASTE_RE = re.compile(r"\b(ngot|beo|mat|lanh|thanh mat|nhe nhang|dam da|man|thom|it duong|giai khat|de uong|tinh tao|buon ngu|chua chua)\b")
FOLLOW_RE = re.compile(r"\b(nua|vay|thi sao|mon do|mon kia|mon nay|cai do|cai kia|cai nay|vua roi|luc nay|tuong tu|giong nhu|con gi|con mon|con cai|the con)\b")
# Từ chỉ chủ đề quán (không gồm các từ chung chung như "gia", "bao nhieu", "tien" để "giá vàng" không lọt vào)
DOMAIN_RE = re.compile(
    r"\b(mon|menu|thuc don|do uong|thuc uong|nuoc uong|uong|banh|an vat|do an|ca phe|tra|sinh to|ep|size|topping|top|ly|"
    r"ngot|beo|duong|sua|caffeine|caffein|cafein|di ung|gluten|trung|hat chia|kem|thach|tran chau|espresso|khat|doi|"
    r"giai khat|tinh tao|buon ngu|ban chay|goi y|tu van|khuyen|goi mon|dat mon|order|chot|gio hang|quan|shop|tiem|phu thu)\b")
OFFTOPIC_HARD_RE = re.compile(
    r"\b(vang|bitcoin|crypto|chung khoan|co phieu|thoi tiet|bong da|tin tuc|chinh tri|bau cu|code|lap trinh|python|javascript|"
    r"toan|giai bai|bai tho|truyen cuoi|xo so|phim|nhac|du lich|suc khoe|benh|thuoc|tinh yeu|nguoi yeu|ky su|dich giup|viet giup|"
    r"lam bai|bai tap|cong thuc nau|nau an)\b")

CAT_PATTERNS = {
    "ca_phe": [r"ca phe"],
    "tra": [r"tra trai cay", r"(?<!doi )(?<!kiem )(?<!tim )tra(?! tien| loi| cuu| gop| lai| hang| xong| giup| xem)"],
    "nuoc_ep": [r"nuoc ep", r"ep"],
    "sinh_to": [r"sinh to"],
    "banh": [r"banh", r"do an", r"an vat", r"an gi", r"an nhe"],
}
# Đồ uống khách hay hỏi nhưng quán KHÔNG bán (không có trong menu.json): trả lời thẳng "chưa có" thay vì để các từ như
# "sữa" bị hiểu nhầm thành bộ lọc dị ứng. Thêm món mới vào menu thì xóa mục tương ứng ở đây.
NOT_SOLD = [(r"tra sua|tra tran chau duong den", "trà sữa"), (r"matcha", "matcha"), (r"bac xiu", "bạc xỉu"),
            (r"latte", "latte"), (r"cappuccino|capuchino", "cappuccino"), (r"smoothie bowl|soda|bia|ruou|ca phe trung|sua chua nep cam", None)]

DRINKS_RE = re.compile(r"\b(do uong|thuc uong|nuoc uong|uong)\b")

ALLERGEN_TOK = {"sua": "sữa", "trung": "trứng", "gluten": "gluten", "dua": "dừa", "hat": "hạt"}
NEG = {"khong", "ko", "k", "dung", "tranh", "kieng", "ung", "tru"}
HAS = {"co", "chua", "gom", "voi"}
NEG_CAT = NEG - {"dung"}  # "dung" = "dùng được" lẫn "đừng": mơ hồ nên bỏ khi xét phủ định tên nhóm
CAFFEINE_TOK = {"caffeine", "caffein", "cafein", "cafeine"}

_NUM = r"(\d{1,3}(?:[.,]\d{3})+|\d+)\s*(k|nghin|ngan|d|vnd|dong)?"
MAX_LIST = 14


# ---------------------------------------------------------------- tiện ích
def _tokens(t):
    return re.findall(r"[a-z0-9]+", t)


def _vnd(num, unit):
    n = int(re.sub(r"[.,]", "", num))
    if unit in ("k", "nghin", "ngan"):
        return n * 1000
    return n if n >= 1000 else None


def _price_filters(t):
    m = re.search(r"\btu\s*" + _NUM + r"\s*(?:den|toi|-)\s*" + _NUM, t)
    if m:
        lo, hi = _vnd(m.group(1), m.group(2)), _vnd(m.group(3), m.group(4))
        return [("ge", lo), ("le", hi)] if lo and hi else []
    out = []
    for op, words in (("lt", r"(?:duoi|it hon|re hon|nho hon|<)"), ("le", r"(?:khong qua|toi da|<=)"),
                      ("gt", r"(?:tren|nhieu hon|dat hon|lon hon|>)"), ("ge", r"(?:toi thieu|it nhat|>=)")):
        m = re.search(words + r"\s*" + _NUM, t)
        v = _vnd(m.group(1), m.group(2)) if m else None
        if v:
            out.append((op, v))
    return out


def _prep_allergen(t):
    """Gộp cụm dễ nhầm trước khi tách từ: 'sữa chua', 'hạt chia', và 'dưa hấu/leo...' (bỏ dấu trùng với 'dừa')."""
    t = t.replace("sua chua", "suachua").replace("hat chia", "hatchia")
    return re.sub(r"\bdua (hau|leo|luoi|gang|chuot)\b", r"dua\1", t)


def _allergen_filters(t):
    """-> (loại trừ, bao gồm). 'không sữa', 'dị ứng hạt' -> loại trừ; 'món nào có sữa' -> bao gồm."""
    toks = _tokens(_prep_allergen(t))
    ex, inc = [], []
    for i, w in enumerate(toks):
        a = ALLERGEN_TOK.get(w)
        if not a:
            continue
        prev = toks[max(0, i - 4):i]
        if any(p in NEG for p in prev):
            ex.append(a)
        elif any(p in HAS for p in prev[-2:]) and a != "dừa":  # "dua" trùng với "dứa" nên không suy ra 'có dừa'
            inc.append(a)
    return ex, inc


def _allergen_mentions(t):
    toks = _tokens(_prep_allergen(t))
    return [ALLERGEN_TOK[w] for w in toks if w in ALLERGEN_TOK]


def _caffeine_filter(t):
    toks = _tokens(t)
    for i, w in enumerate(toks):
        if w in CAFFEINE_TOK:
            prev = toks[max(0, i - 4):i]
            if any(p in NEG for p in prev):
                return "none"
            if any(p in ("it", "thap") for p in prev):
                return None  # "ít caffeine" là yêu cầu mềm -> để LLM
            return "has"
    return None


def _category_split(menu, t):
    """-> (nhóm khách MUỐN, nhóm khách LOẠI). Có từ phủ định trong 4 từ liền trước tên nhóm (cùng mệnh đề) thì là LOẠI:
    'không uống được cà phê', 'tránh trà', 'không muốn uống nước ép'."""
    inc, exc = [], []
    for cid in menu["categories"]:
        pats = CAT_PATTERNS.get(cid) or [re.escape(norm(menu["categories"][cid]))]
        for p in pats:
            m = re.search(rf"\b(?:{p})\b", t)
            if m:
                clause = re.split(r"[,.;:!?]", t[:m.start()])[-1]
                (exc if any(w in NEG_CAT for w in _tokens(clause)[-4:]) else inc).append(cid)
                break
    if DRINKS_RE.search(t) and not inc:
        inc = [c for c in menu["categories"] if c != "banh" and c not in exc]
    return inc, exc


def _categories(menu, t):
    return _category_split(menu, t)[0]


def _sorted(menu, items):
    order = {c: i for i, c in enumerate(menu["categories"])}
    return sorted(items, key=lambda it: (order.get(it["category"], 99), it["price"]))


def _candidates(menu, t):
    """Lọc món theo điều kiện CỨNG trong câu. -> (danh sách, mô tả điều kiện, có lọc dị ứng?) hoặc None."""
    items, parts, allergen_used, hard = list(menu["items"]), [], False, False

    cats, exc = _category_split(menu, t)
    if cats:
        hard = True
        items = [i for i in items if i["category"] in cats]
        names = [menu["categories"][c] for c in cats]
        parts.append("nhóm " + ", ".join(names) if len(cats) < len(menu["categories"]) - 1 else "đồ uống")
    if exc:
        hard = True
        items = [i for i in items if i["category"] not in exc]
        if not cats:
            parts.append("không thuộc nhóm " + ", ".join(menu["categories"][c] for c in exc))

    caf = _caffeine_filter(t)
    if caf:
        hard = True
        want = caf == "has"
        items = [i for i in items if bool(i.get("caffeine")) == want]
        parts.append("có caffeine" if want else "không caffeine")

    ex, inc = _allergen_filters(t)
    for a in dict.fromkeys(ex):
        hard = allergen_used = True
        items = [i for i in items if a not in i.get("allergens", [])]
        parts.append(f"không chứa {a}")
    for a in dict.fromkeys(inc):
        hard = allergen_used = True
        items = [i for i in items if a in i.get("allergens", [])]
        parts.append(f"có chứa {a}")

    for op, v in _price_filters(t):
        hard = True
        f = {"lt": lambda p: p < v, "le": lambda p: p <= v, "gt": lambda p: p > v, "ge": lambda p: p >= v}[op]
        items = [i for i in items if f(i["price"])]
        parts.append({"lt": "dưới ", "le": "tối đa ", "gt": "trên ", "ge": "từ "}[op] + fmt_vnd(v))

    if re.search(r"\b(re nhat|gia thap nhat|thap nhat)\b", t) and items:
        hard = True
        lo = min(i["price"] for i in items)
        items = [i for i in items if i["price"] == lo]
        parts.append("rẻ nhất")
    elif re.search(r"\b(dat nhat|mac nhat|gia cao nhat|cao nhat)\b", t) and items:
        hard = True
        hi = max(i["price"] for i in items)
        items = [i for i in items if i["price"] == hi]
        parts.append("đắt nhất")

    return (_sorted(menu, items), parts, allergen_used) if hard else None


# Câu kiểu "Có bánh socola không?": khách hỏi MỘT món cụ thể trong nhóm. Nếu từ mô tả (socola) không có trong menu
# thì phải nói thẳng "chưa có", không được liệt kê cả nhóm như thể đã trả lời.
_EXIST_END_RE = re.compile(r"\b(khong|ko|ha|chua)\s*$")
_WH_RE = re.compile(r"\b(nao|gi|loai|nhung|cac|nhieu|may|bao nhieu)\b")
_FILLER = {"co", "khong", "ko", "ha", "chua", "quan", "ban", "shop", "tiem", "con", "ben", "cua", "minh", "cho", "a", "nhe",
           "nha", "vay", "day", "oi", "la", "thi", "hom", "nay", "mon", "loai", "muc", "nhom", "do", "uong", "an", "vat",
           "ca", "phe", "tra", "trai", "cay", "nuoc", "ep", "sinh", "to", "banh", "size", "m", "l"}


def _unknown_item_words(menu, text, t):
    """Trả về các từ lạ (giữ dấu như khách gõ) nếu câu có dạng 'có <nhóm> <từ lạ> không', ngược lại trả về []."""
    if not re.search(r"\bco\b", t) or not _EXIST_END_RE.search(t.rstrip(" ?!.")):
        return []
    if _WH_RE.search(t) or TASTE_RE.search(t) or SUBJECTIVE_RE.search(t) or ATTR_RE.search(t):
        return []
    if not _categories(menu, t):
        return []
    known = set(_FILLER)
    for cid, name in menu["categories"].items():
        known.update(_tokens(norm(name)))
        for pat in CAT_PATTERNS.get(cid, []):
            known.update(w for w in _tokens(pat) if w.isalpha())
    for tp in menu["toppings"]:
        known.update(_tokens(norm(tp["name"])))
    for it in menu["items"]:
        known.update(_tokens(norm(it["name"])))
    words = re.findall(r"\w+", text.lower())
    left = [w for w in words if norm(w) not in known and norm(w)]
    return left if 1 <= len(left) <= 3 else []


def _item_line(it):
    return f"- {it['name']}: {fmt_vnd(it['price'])}"


def _list_reply(menu, items, parts, allergen_used):
    if not items:
        return "Mình xem kỹ menu rồi nhưng chưa có món nào " + (" và ".join(parts) or "phù hợp") + " ạ. Bạn thử nới điều kiện giúp mình nhé."
    shown = items[:MAX_LIST]
    head = "Các món " + (", ".join(parts) if parts else "phù hợp") + " (giá size M):"
    lines = [_item_line(i) for i in shown]
    if len(items) > MAX_LIST:
        lines.append(f"... và {len(items) - MAX_LIST} món khác.")
    size_note = " (món bánh không có size)" if any(not i["sized"] for i in shown) else ""
    foot = f"Size L +{fmt_vnd(menu['sizes']['L'])}{size_note}. Muốn đặt thì gõ ví dụ “cho mình 1 {shown[0]['name'].lower()}”."
    if allergen_used:
        foot += " Nếu bạn dị ứng nặng, nhớ báo nhân viên quán để chắc chắn nhé."
    return head + "\n" + "\n".join(lines) + "\n" + foot


def _unique_items(menu, found):
    seen, out = set(), []
    for _s, _e, iid in found:
        if iid not in seen:
            seen.add(iid)
            out.append(menu["_items"][iid])
    return out


def _allergen_text(it):
    return ", ".join(it.get("allergens", [])) or "không có"


def _item_card(menu, it):
    size = f", size L {fmt_vnd(it['price'] + menu['sizes']['L'])}" if it["sized"] else " (không có size)"
    caf = ("có" if it["caffeine"] else "không") if "caffeine" in it else None
    return (f"{it['name']} – {fmt_vnd(it['price'])}{size}\n{it['description']} Dị ứng: {_allergen_text(it)}."
            + (f" Caffeine: {caf}." if caf else "") + f"\nMuốn đặt thì gõ “cho mình 1 {it['name'].lower()}” nhé.")


def _topping_line(tp, menu):
    cats = ", ".join(menu["categories"][c] for c in tp["categories"])
    al = f" [chứa {', '.join(tp['allergens'])}]" if tp.get("allergens") else ""
    return f"- {tp['name']}: +{fmt_vnd(tp['price'])}{al} (dùng cho: {cats})"


def _tname(menu, tid):
    n = menu["_toppings"][tid]["name"].lower()
    return n[5:] if n.startswith("thêm ") else n


def _total_reply(menu, text):
    try:
        lines = parse_order(menu, text)
        mentioned = set(_find_toppings(menu, norm(text)))
        counted = {tid for ln in lines for tid in ln["toppings"]}
        if mentioned - counted:
            return ("Mình chưa chắc topping đó đi kèm món nào. Bạn nói theo mẫu “cho mình 1 nâu đá thêm kem cheese, tổng bao nhiêu” "
                    "(topping đứng SAU tên món) để mình tính chính xác nhé.")
        rows, total = [], 0
        for ln in lines:
            it = menu["_items"][ln["item_id"]]
            sub = price_line(menu, ln["item_id"], ln["size"], ln["toppings"], ln["qty"])
            parts = [fmt_vnd(it["price"])]
            if it["sized"] and menu["sizes"][ln["size"]]:
                parts.append(f"size {ln['size']} {fmt_vnd(menu['sizes'][ln['size']])}")
            tnames = [menu["_toppings"][tid]["name"] for tid in ln["toppings"]]
            parts += [f"{menu['_toppings'][tid]['name']} {fmt_vnd(menu['_toppings'][tid]['price'])}" for tid in ln["toppings"]]
            label = f"{ln['qty']} x {it['name']}" + (f" size {ln['size']}" if it["sized"] else "") + (", " + ", ".join(tnames) if tnames else "")
            if len(parts) == 1 and ln["qty"] == 1:
                calc = fmt_vnd(sub)
            else:
                calc = ("(" + " + ".join(parts) + ")" if len(parts) > 1 else parts[0]) + (f" x {ln['qty']}" if ln["qty"] > 1 else "") + f" = {fmt_vnd(sub)}"
            rows.append(f"- {label}: {calc}")
            total += sub
    except ValueError as e:
        return f"Mình chưa tính được: {e}."
    first = lines[0]
    fit = menu["_items"][first["item_id"]]
    ex = f"cho mình {first['qty']} {fit['name'].lower()}" + (f" size {first['size']}" if fit["sized"] and first["size"] == "L" else "")
    ex += (" thêm " + ", ".join(_tname(menu, t) for t in first["toppings"])) if first["toppings"] else ""
    return "Mình tính giúp bạn nhé:\n" + "\n".join(rows) + f"\nTổng cộng: {fmt_vnd(total)}\nMuốn đặt thì gõ “{ex}” (không kèm câu hỏi giá) để mình thêm vào giỏ nhé."


# ---------------------------------------------------------------- hàm chính
def is_followup(t, has_history):
    if not has_history:
        return False
    return bool(FOLLOW_RE.search(t)) or len(_tokens(t)) <= 3


def answer(menu, text, has_history=False):
    t = norm(text)
    t0 = " ".join(_tokens(t))
    shop = menu.get("shop_name", "quán")
    follow = is_followup(t, has_history)

    # 1) chào hỏi / cảm ơn / hỏi bot là ai
    if re.fullmatch(GREET, t0) or re.fullmatch(OPENER, t0):
        return _reply(f"Chào bạn, mình là trợ lý của {shop}. Bạn muốn xem menu, hỏi giá, tìm món không caffeine hay đặt món nào ạ?", "chitchat", False)
    if re.fullmatch(THANKS, t0):
        return _reply("Dạ không có gì ạ! Cần gì thêm bạn cứ nhắn mình nhé.", "chitchat", False)
    if re.fullmatch(BYE, t0):
        return _reply(f"Cảm ơn bạn đã ghé {shop}. Hẹn gặp lại bạn nhé!", "chitchat", False)
    if WHO_RE.search(t):
        return _reply(f"Mình là trợ lý AI của {shop}: giúp bạn xem menu, hỏi giá, topping, dị ứng, caffeine, gợi ý món và đặt món.", "chitchat", False)
    if HOWRU_RE.search(t):
        return _reply(f"Mình khỏe, cảm ơn bạn đã hỏi! Bạn cần mình tư vấn món gì ở {shop} không ạ?", "chitchat", False)
    if HELP_RE.search(t):
        return _reply("Bạn có thể: hỏi giá (“nâu đá giá bao nhiêu”), lọc món (“món nào không caffeine”, “có món nào dưới 40k”), "
                      "hỏi topping, hoặc đặt món bằng cách gõ “cho mình 2 cà phê muối size L”, rồi “chốt đơn”.", "chitchat", False)

    # 2) thông tin quán KHÔNG có trong dữ liệu -> nói thật, không bịa
    for label, pat in SHOP_INFO:
        if re.search(rf"\b(?:{pat})\b", t):
            known = (menu.get("shop_info") or {}).get(label)  # chủ quán điền vào menu.json -> trả lời thật, không còn "chưa có"
            if known:
                return _reply(f"{label[0].upper() + label[1:]}: {known}", "info", False)
            return _reply(f"Mình chưa có thông tin về {label} của quán trong hệ thống nên không dám nói bừa. "
                          "Bạn hỏi trực tiếp nhân viên quán nhé. Mình hỗ trợ xem menu, giá, topping, tư vấn món và đặt món.", "info", False)

    for pat, label in NOT_SOLD:
        m = re.search(rf"\b(?:{pat})\b", t)
        if m and not _find_items(menu, t):
            what = label or "món đó"
            cats = ", ".join(menu["categories"].values())
            return _reply(f"Mình xem menu rồi, quán chưa có {what} ạ. Quán đang có các nhóm: {cats}. "
                          "Bạn gõ “món nào không caffeine?” hoặc “có món nào dưới 40k?” để mình lọc giúp nhé.")

    found = _find_items(menu, t)
    items = _unique_items(menu, found)
    tops = _find_toppings(menu, t)

    # 3) câu hỏi về MỘT/NHIỀU món cụ thể
    if items:
        if SUBJECTIVE_RE.search(t) or (len(items) >= 2 and re.search(r"\b(hay|hay la)\b", t)):
            return _llm(followup=follow)  # so sánh, "món nào ngon" -> tư vấn mở
        if PRICE_RE.search(t) and (TOTAL_RE.search(t) or _has_order_intent(menu, t)):
            return _reply(_total_reply(menu, text))
        if TOPPING_RE.search(t) or tops:
            it = items[0]
            allowed = [tp for tp in menu["toppings"] if it["category"] in tp["categories"]]
            if tops:
                msgs = []
                for tid in tops:
                    tp = menu["_toppings"][tid]
                    if it["category"] in tp["categories"]:
                        msgs.append(f"Được ạ, {tp['name']} (+{fmt_vnd(tp['price'])}) dùng được cho {it['name']}.")
                    else:
                        msgs.append(f"{tp['name']} không dùng được cho {it['name']}.")
                return _reply(" ".join(msgs) + "\nTopping dùng được cho món này:\n" + "\n".join(_topping_line(tp, menu) for tp in allowed))
            return _reply(f"Topping dùng được cho {it['name']}:\n" + "\n".join(_topping_line(tp, menu) for tp in allowed))
        mentions, caf_mentioned = _allergen_mentions(t), any(w in CAFFEINE_TOK for w in _tokens(t))
        if mentions or caf_mentioned:
            rows = []
            for it in items:
                bits = [("có" if a in it.get("allergens", []) else "không") + f" chứa {a}" for a in dict.fromkeys(mentions)]
                if caf_mentioned and "caffeine" in it:
                    bits.append("có caffeine" if it["caffeine"] else "không có caffeine")
                rows.append(f"- {it['name']}: " + "; ".join(bits) + f" (dị ứng ghi trong menu: {_allergen_text(it)})")
            foot = "\nNếu bạn dị ứng nặng, nhớ báo nhân viên quán để chắc chắn nhé." if mentions else ""
            return _reply("Theo menu của quán:\n" + "\n".join(rows) + foot)
        if PRICE_RE.search(t):
            rows = []
            for it in items:
                if it["sized"]:
                    rows.append(f"- {it['name']}: {fmt_vnd(it['price'])} (size M), {fmt_vnd(it['price'] + menu['sizes']['L'])} (size L)")
                else:
                    rows.append(f"- {it['name']}: {fmt_vnd(it['price'])} (không có size)")
            return _reply("\n".join(rows) + f"\nMuốn đặt thì gõ “cho mình 1 {items[0]['name'].lower()}” nhé.")
        if len(items) == 1 and (ATTR_RE.search(t) or len(_tokens(t)) <= 4):
            return _reply(_item_card(menu, items[0]))
        return _llm(followup=follow)

    # 4) topping (không nhắc món)
    if TOPPING_RE.search(t) or tops:
        cats = _categories(menu, t)
        if tops and PRICE_RE.search(t):
            return _reply("\n".join(_topping_line(menu["_toppings"][tid], menu) for tid in tops))
        pool = [tp for tp in menu["toppings"] if not cats or any(c in tp["categories"] for c in cats)]
        head = "Topping dùng được cho " + ", ".join(menu["categories"][c] for c in cats) if cats else "Các topping của quán"
        return _reply(head + ":\n" + "\n".join(_topping_line(tp, menu) for tp in pool))

    # 5) size / phụ thu
    if SIZE_RE.search(t) and not _categories(menu, t) and not _price_filters(t):
        return _reply(f"Quán có size M (giá niêm yết) và size L (+{fmt_vnd(menu['sizes']['L'])}). Món bánh không có size.")

    # 6) lọc món theo điều kiện cứng (danh mục, caffeine, dị ứng, giá)
    cand = _candidates(menu, t)
    if cand and not follow:
        its, parts, allergen_used = cand
        unk = _unknown_item_words(menu, text, t) if len(parts) == 1 and parts[0].startswith("nhóm ") else []
        if unk:
            cats = [menu["categories"][c] for c in _categories(menu, t)]
            names = ", ".join(i["name"] for i in its[:8])
            return _reply(f"Mình xem menu rồi, quán chưa có món “{cats[0].lower()} {' '.join(unk)}” ạ. "
                          f"Nhóm {', '.join(cats)} hiện có: {names}. Bạn muốn xem giá hay đặt món nào không?")
        if parts == ["đồ uống"]:
            return _llm(followup=False)  # "uống gì", "trời nóng uống gì": tư vấn mở, liệt kê cả 16 đồ uống không giúp gì
        if TASTE_RE.search(t) or SUBJECTIVE_RE.search(t):
            hint = "Món đúng điều kiện " + ", ".join(parts) + " (giá size M): " + "; ".join(f"{i['name']} {fmt_vnd(i['price'])}" for i in its[:20]) \
                + ". Chỉ chọn trong danh sách này." if its else "Không có món nào đúng điều kiện " + ", ".join(parts) + "."
            return _llm(hint=hint, followup=False)
        return _reply(_list_reply(menu, its, parts, allergen_used))

    # 7) yêu cầu liệt kê toàn bộ menu: lấy dữ liệu trực tiếp, không chuyển sang LLM
    if LIST_ALL_RE.search(t):
        rows = [_item_line(it) + (" (không có size)" if not it["sized"] else "")
                for it in _sorted(menu, menu["items"])]
        return _reply("Toàn bộ menu của quán (giá size M):\n" + "\n".join(rows)
                      + f"\nSize L +{fmt_vnd(menu['sizes']['L'])} cho món có size."
                      )

    # 8) bán chạy / tổng quan menu
    if BEST_RE.search(t) and not follow:
        best = [menu["_items"][i] for i in menu.get("bestsellers_seed", []) if i in menu["_items"]]
        if best:
            return _reply("Món bán chạy của quán:\n" + "\n".join(f"- {i['name']}: {fmt_vnd(i['price'])} – {i['description']}" for i in best))
    if OVERVIEW_RE.search(t) and not TASTE_RE.search(t) and not follow:
        cnt = {c: sum(1 for i in menu["items"] if i["category"] == c) for c in menu["categories"]}
        groups = "\n".join(f"- {n}: {cnt[c]} món" for c, n in menu["categories"].items())
        return _reply(f"Menu của {shop} có {len(menu['categories'])} nhóm:\n{groups}\nBạn muốn xem nhóm nào, hay lọc theo giá/caffeine/dị ứng ạ?")

    # 9) câu không thuộc các tầng trên. KHÔNG chặn nữa:
    #    - kể hoàn cảnh ("trời nóng quá", "mua cho bạn gái nên chọn gì") -> gợi ý món (LLM + menu)
    #    - có từ khóa quán / câu nối tiếp                                -> LLM + menu
    #    - còn lại (ngoài lề thật sự)                                    -> LLM với prompt tổng quát (general=True)
    domain = bool(DOMAIN_RE.search(t))
    hard_off = bool(OFFTOPIC_HARD_RE.search(t))
    if not hard_off and (STATE_RE.search(t) or (SOCIAL_RE.search(t) and ASKPICK_RE.search(t))):
        return _llm(followup=follow)
    if hard_off and not domain:
        follow = False
    if not domain and not follow:
        return _llm(general=True)
    return _llm(followup=follow)
