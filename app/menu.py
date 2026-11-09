"""Dữ liệu menu + tính tiền. Backend là nơi DUY NHẤT tính tiền, AI không được tự tính."""
import json
import re
import unicodedata
from pathlib import Path

MENU_PATH = Path(__file__).resolve().parent.parent / "menu.json"


def load_menu(path=MENU_PATH):
    with open(path, encoding="utf-8") as f:
        menu = json.load(f)
    menu["_items"] = {i["id"]: i for i in menu["items"]}
    menu["_toppings"] = {t["id"]: t for t in menu["toppings"]}
    return menu


def fmt_vnd(n):
    return f"{n:,}".replace(",", ".") + "đ"


def norm(s):
    """Chữ thường, bỏ dấu tiếng Việt, gọn khoảng trắng. 'Cà phê muối' -> 'ca phe muoi'."""
    s = s.lower().replace("đ", "d")
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"\bcf\b|\bcafe\b|\bcoffee\b", "ca phe", s)
    return re.sub(r"\s+", " ", s).strip()


def price_line(menu, item_id, size="M", topping_ids=(), qty=1):
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
    """Menu dạng text gọn (có mô tả ngắn để model trả lời 'ít ngọt', 'béo'...)."""
    lines = []
    for cat_id, cat_name in menu["categories"].items():
        lines.append(f"[{cat_name}]")
        for it in menu["items"]:
            if it["category"] != cat_id:
                continue
            size_note = f" (size L +{fmt_vnd(menu['sizes']['L'])})" if it["sized"] else ""
            tags = []
            if it.get("allergens"):
                tags.append("chứa " + ", ".join(it["allergens"]))
            if cat_id != "banh" and "caffeine" in it:
                tags.append("có caffeine" if it["caffeine"] else "không caffeine")
            tag_note = f" [{'; '.join(tags)}]" if tags else ""
            lines.append(f"- {it['name']} {fmt_vnd(it['price'])}{size_note}{tag_note}: {it['description']}")
        tops = [t for t in menu["toppings"] if cat_id in t["categories"]]
        if tops:
            lines.append("  Topping: " + ", ".join(
                f"{t['name']} +{fmt_vnd(t['price'])}" + (f" [chứa {', '.join(t['allergens'])}]" if t.get("allergens") else "")
                for t in tops))
    return "\n".join(lines)


def build_system_prompt(menu, bestsellers=None):
    bestsellers = bestsellers or menu.get("bestsellers_seed", ("ca_phe_muoi", "tra_dao_cam_sa"))
    shop = menu.get("shop_name", "quán cà phê")
    best = ", ".join(menu["_items"][i]["name"] for i in bestsellers if i in menu["_items"])
    return (
        f"Bạn là nhân viên tư vấn của {shop}. Luôn trả lời tiếng Việt, thân thiện, tối đa 3 câu ngắn.\n\n"
        "PHẠM VI CHÍNH: menu, giá, topping, size, dị ứng, caffeine, gợi ý món và cách đặt món của quán.\n"
        "Nếu khách hỏi chuyện hoàn toàn ngoài quán: trả lời ngắn đúng câu hỏi nếu bạn biết chắc; nếu cần số liệu thời gian thực "
        "(thời tiết, giá vàng, tin tức) thì nói thật là bạn không tra cứu được, không đoán; rồi mời khách hỏi về menu.\n"
        "Khách kể hoàn cảnh (trời nóng, mệt, mất ngủ, mua cho người thân...) hoặc nói món KHÔNG uống được/không thích = đang xin gợi ý: "
        "gợi ý món phù hợp trong MENU và TRÁNH đúng thứ khách nói không dùng (vd 'không uống được cà phê' thì không gợi ý món cà phê).\n"
        "Thông tin quán không có trong MENU (giờ mở cửa, địa chỉ, khuyến mãi): nói thật là bạn chưa có thông tin, đừng đoán.\n\n"
        "QUY TẮC:\n"
        "1. Chỉ dùng món, giá, topping có trong MENU. Không có trong MENU thì nói quán chưa có món đó.\n"
        "2. KHÔNG tự cộng hay tính tổng tiền. Khi khách muốn đặt món hoặc tính tiền, bảo khách gõ: "
        "'cho mình 2 cà phê muối size L' hoặc 'tính tiền'.\n"
        "3. Dị ứng và caffeine lấy đúng theo MENU; khách dị ứng nặng thì dặn báo nhân viên quán.\n"
        "4. Khi khách yêu cầu xem toàn bộ menu, hãy liệt kê các món dựa chính xác vào MENU được cung cấp.\n"
        "5. Khi khách yêu cầu gợi ý món, chọn 1-3 món phù hợp, nêu tên, giá và lý do ngắn dựa vào mô tả; không tự bịa món hoặc giá.\n"
        "6. Nếu tin nhắn có dòng 'Dữ liệu đã lọc từ hệ thống', chỉ được chọn món trong danh sách đó.\n"
        "7. Câu mơ hồ: hỏi lại 1 câu ngắn.\n\n"
        "VÍ DỤ:\n"
        "Khách: Hôm nay trời nóng quá.\n"
        "Bạn: Trời nóng thì bạn thử Nước ép dưa hấu (39.000đ), mát và ít ngọt nhé. Bạn có muốn đặt không?\n"
        "Khách: Món nào ít ngọt?\n"
        "Bạn: Bạn thử Nước ép dưa hấu (39.000đ), ít đường và mát. Bạn có muốn đặt không?\n\n"
        f"MENU:\n{menu_to_prompt(menu)}\n\nMón bán chạy: {best}\n"
    )


def build_general_prompt(menu):
    """Prompt cho câu NGOÀI menu (kiến thức chung, trò chuyện, câu hỏi bất kỳ): trả lời đúng câu được hỏi, nhưng không bịa."""
    shop = menu.get("shop_name", "quán cà phê")
    return (
        f"Bạn là trợ lý thân thiện của {shop}. Luôn trả lời tiếng Việt, ngắn gọn (tối đa 4 câu), đúng trọng tâm câu khách hỏi.\n\n"
        "Khách đang hỏi một câu không nằm trong menu. Hãy TRẢ LỜI câu đó, đừng từ chối chỉ vì nó ngoài chuyện quán.\n"
        "QUY TẮC TRUNG THỰC:\n"
        "1. Điều bạn biết chắc (kiến thức phổ thông, trò chuyện, giải thích, toán đơn giản): trả lời thẳng.\n"
        "2. Thông tin cập nhật theo thời gian thực (thời tiết hôm nay, giá vàng/tỷ giá/chứng khoán/bitcoin, tin tức, kết quả trận đấu): "
        "bạn KHÔNG có công cụ tra cứu, hãy nói rõ điều đó và chỉ nơi tra cứu phù hợp. TUYỆT ĐỐI không đoán con số.\n"
        "3. Không biết hoặc không chắc thì nói 'mình không chắc'. Không bịa sự kiện, số liệu, tên người, đường dẫn.\n"
        "4. Thông tin riêng của quán (giờ mở cửa, địa chỉ, khuyến mãi...): nói bạn chưa có thông tin.\n"
        "5. Y tế, pháp lý, tài chính: chỉ nói thông tin chung và khuyên hỏi chuyên gia.\n"
        "6. Yêu cầu dài (viết bài, viết chương trình dài): làm bản ngắn gọn hoặc nói rõ mình chỉ trả lời ngắn ở đây.\n"
        "7. Không tự nhắc menu hay giá. Chỉ khi hợp ngữ cảnh mới mời khách hỏi thêm về menu, tối đa 1 câu.\n"
    )
