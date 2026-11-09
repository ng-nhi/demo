"""Test lớp trả lời bằng code (app/faq.py). Không cần FastAPI hay model: pytest -q tests/test_faq.py"""
import pytest

from app import faq
from app.menu import load_menu

MENU = load_menu()


def ask(text, history=False):
    return faq.answer(MENU, text, history)


@pytest.mark.parametrize("q,must", [
    ("nâu đá giá bao nhiêu", ["29.000đ", "39.000đ"]),
    ("Cà phê muối giá bao nhiêu?", ["35.000đ", "45.000đ"]),
    ("Kem cheese giá bao nhiêu?", ["12.000đ"]),
    ("Size L phụ thu bao nhiêu?", ["10.000đ"]),
    ("Cho mình 2 trà đào cam sả size L, tổng bao nhiêu?", ["110.000đ"]),
    ("Cho mình 3 macaron, tổng bao nhiêu?", ["90.000đ"]),
    ("Cho mình 1 trà mãng cầu size M thêm trân châu đen, tổng bao nhiêu?", ["52.000đ"]),
    ("Cho mình 1 sinh tố bơ size L thêm hạt chia, tổng bao nhiêu?", ["64.000đ"]),
    ("Cho mình 2 nâu đá size M thêm 1 shot espresso mỗi ly, tổng bao nhiêu?", ["78.000đ"]),
])
def test_prices_and_totals_are_exact(q, must):
    r = ask(q)
    assert r.reply is not None and r.kind == "faq"
    for s in must:
        assert s in r.reply, (q, r.reply)


def test_invalid_topping_is_rejected():
    assert "không dùng được" in ask("nâu đá thêm thạch đào giá bao nhiêu").reply
    assert "không dùng được" in ask("nâu đá thêm thạch đào được không").reply


def test_topping_before_item_is_not_silently_ignored():
    r = ask("kem cheese thêm vào nâu đá giá bao nhiêu")
    assert "29.000đ" not in r.reply and "SAU tên món" in r.reply  # không được báo giá thiếu topping


def test_caffeine_and_allergen_filters():
    no_caf = ask("món nào không caffeine?").reply
    assert "Nước ép dưa hấu" in no_caf and "Tiramisu" not in no_caf and "Cà phê muối" not in no_caf
    no_milk = ask("mình dị ứng sữa thì uống gì?").reply
    assert "Cà phê đen" in no_milk and "Sinh tố xoài" in no_milk
    assert "Cà phê muối" not in no_milk and "Sinh tố bơ" not in no_milk and "Bánh" not in no_milk
    gluten = ask("Bánh nào có chứa gluten?").reply
    assert "Tiramisu" in gluten and "Macaron" not in gluten


def test_price_filters():
    r = ask("có món nào dưới 40k?").reply
    assert "Nâu đá" in r and "Trà đào cam sả" not in r
    assert "Su kem" in ask("món nào rẻ nhất?").reply
    assert "Nước ép lựu" in ask("món nào đắt nhất?").reply


def test_category_bestseller_overview_topping():
    assert "Nâu đá" in ask("Quán có những món cà phê nào?").reply
    best = ask("Món nào bán chạy nhất?").reply
    assert all(n in best for n in ("Cà phê muối", "Trà đào cam sả", "Sinh tố bơ"))
    over = ask("quán có những nhóm món nào?").reply
    assert all(n in over for n in ("Cà phê", "Trà trái cây", "Nước ép", "Sinh tố", "Bánh"))
    tp = ask("Topping nào dùng được cho sinh tố?").reply
    assert "Hạt chia" in tp and "Sữa chua" in tp and "Trân châu đen" not in tp



@pytest.mark.parametrize("q", [
    "Liệt kê tất cả các món",
    "Cho mình xem toàn bộ menu",
    "Mình muốn xem hết thực đơn",
    "Quán đang bán tất cả món nào?",
])
def test_list_all_menu(q):
    r = ask(q)
    assert r.reply is not None and r.kind == "faq", (q, r)
    for item in MENU["items"]:
        assert item["name"] in r.reply, (q, item["name"], r.reply)

def test_item_attribute_questions():
    assert "có chứa sữa" in ask("cà phê muối có sữa không?").reply
    assert "không chứa sữa" in ask("cà phê đen có sữa không").reply
    assert "có caffeine" in ask("tiramisu có caffeine không").reply


@pytest.mark.parametrize("q", ["Hôm nay giá vàng bao nhiêu?", "thời tiết hôm nay thế nào", "viết giúp mình đoạn code python",
                                "bạn nghĩ gì về bitcoin", "thủ đô nước Pháp là gì"])
def test_off_topic_goes_to_general_llm_not_refused(q):
    r = ask(q)
    assert r.reply is None and r.kind == "llm" and r.general is True, q


@pytest.mark.parametrize("q", ["Hôm nay trời nóng quá", "Mình muốn mua cho bạn gái, nên chọn gì", "mình đang rất mệt"])
def test_circumstance_is_menu_advice_not_off_topic(q):
    r = ask(q)
    assert r.reply is None and r.general is False, q  # LLM + menu: gợi ý món


def test_shop_info_is_not_invented():
    for q in ("quán mở cửa lúc mấy giờ?", "wifi quán là gì", "quán có giao hàng không", "địa chỉ quán ở đâu"):
        r = ask(q)
        assert r.kind == "info" and "chưa có thông tin" in r.reply, q


def test_small_talk():
    assert ask("xin chào").kind == "chitchat" and ask("cảm ơn bạn").kind == "chitchat" and ask("bạn là ai").kind == "chitchat"


def test_open_advice_goes_to_llm_with_filtered_hint():
    for q in ("gợi ý món mát cho mình", "cho mình hỏi món nào ít ngọt?", "nên uống cà phê muối hay nâu đá", "trời nóng uống gì"):
        assert ask(q).reply is None, q
    r = ask("Món nào ít ngọt dưới 40k?")
    assert r.reply is None and "Nước ép dưa hấu" in r.hint and "Trà đào cam sả" not in r.hint


def test_follow_up_uses_history_but_off_topic_uses_general_prompt():
    r = ask("còn món nào nữa không?", history=True)
    assert r.reply is None and r.followup is True
    g = ask("giá vàng?", history=True)
    assert g.reply is None and g.general is True and g.followup is False


@pytest.mark.parametrize("q,word", [
    ("Có bánh socola không", "socola"),
    ("Có bánh socola không?", "socola"),
    ("Có sinh tố socola không", "socola"),
    ("Có cà phê matcha không?", "matcha"),
    ("Có nước ép táo không", "táo"),
])
def test_unknown_item_in_known_category_says_not_available(q, word):
    r = ask(q)
    assert r.reply is not None and "chưa có" in r.reply and word in r.reply, r.reply
    assert "Các món nhóm" not in r.reply  # không được liệt kê cả nhóm như thể đã trả lời


@pytest.mark.parametrize("q", [
    "Quán có bánh nào không",
    "Có bánh nào dưới 30k không",
    "Có bánh không chứa gluten không",
    "Có trà nào có caffeine không",
    "Có bánh tiramisu không",
])
def test_real_category_questions_still_listed_or_answered(q):
    r = ask(q)
    assert r.reply is not None and "chưa có món “" not in r.reply, r.reply


def test_size_note_only_when_list_has_cakes():
    assert "món bánh không có size" not in ask("Quán có những món cà phê nào?").reply
    assert "món bánh không có size" in ask("Quán có bánh nào không").reply


def test_eval_set_has_enough_cases_and_offline_ones_pass():
    """Bộ câu hỏi chấm chatbot (tests/eval_questions.json) phải đủ 45-60 câu và mọi câu trả lời bằng code phải đạt."""
    import json
    from pathlib import Path
    cases = json.loads((Path(__file__).parent / "eval_questions.json").read_text(encoding="utf-8"))
    assert 45 <= len(cases) <= 60
    from scripts.eval_chat import check
    for c in cases:
        r = ask(c["q"])
        if c["source"] == "llm":
            assert r.reply is None, c["q"]  # phải đi qua LLM
            continue
        assert r.reply is not None, c["q"]
        assert check(c, r.reply, r.kind) == [], (c["q"], r.reply)


@pytest.mark.parametrize("q,cat_out", [
    ("Mình không uống được cà phê, gợi ý giúp mình món khác.", "Cà phê"),
    ("Mình bị mất ngủ, không uống cà phê, gợi ý món khác", "Cà phê"),
    ("Không muốn uống trà, gợi ý món khác", "Trà trái cây"),
])
def test_negated_category_is_excluded_from_hint(q, cat_out):
    r = ask(q)
    assert r.reply is None and r.hint and cat_out not in r.hint.split("(giá size M):")[0].replace("không thuộc nhóm " + cat_out, "")
    assert "Cà phê đen" not in r.hint if cat_out == "Cà phê" else "Trà đào cam sả" not in r.hint


def test_negated_category_list_has_no_excluded_items():
    r = ask("Mình không uống được cà phê").reply
    assert "Cà phê" not in r and "Nâu đá" not in r and "Nước ép" in r


def test_positive_category_still_works():
    assert "Nâu đá" in ask("Quán có những món cà phê nào?").reply
    assert "Trà đào cam sả" in ask("cho mình xem trà").reply


def test_doi_tra_is_not_tea_and_dua_hau_is_not_coconut():
    r = ask("Cho mình hỏi về chính sách đổi trả")
    assert r.kind == "info" and "Trà đào" not in r.reply
    r = ask("Nước ép dưa hấu có nhiều đường không")
    assert "không chứa dừa" not in (r.reply or "")


def test_how_are_you_is_small_talk():
    assert ask("Bạn khỏe không?").kind == "chitchat"


def test_shop_info_from_menu_json_is_used_when_present():
    menu = dict(MENU, shop_info={"giờ mở cửa": "7h - 22h hằng ngày"})
    r = faq.answer(menu, "quán mở cửa lúc mấy giờ?", False)
    assert r.kind == "info" and "7h - 22h" in r.reply
