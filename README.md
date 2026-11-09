# Quán Cà Phê AI – Phiên bản V0 (Người 1)

Web chat hỏi đáp menu, tư vấn món, tính tiền, giỏ hàng và chốt đơn. AI chạy local bằng `llama-server` + Qwen2.5-3B-Instruct (GGUF Q4_K_M).

## Chạy bằng MỘT lệnh (khuyên dùng)
```bash
python start.py            # tự tìm llama-server + model; thiếu thì tự chạy AI giả lập
python start.py --mock     # ép AI giả lập (không cần model, không cần GPU)
python start.py --help     # xem các tùy chọn (--model, --ngl 99 cho GPU, --llm-port, --parallel ...)
```
Windows: bấm đúp `start.bat` (dùng `.venv` nếu có). Mac/Linux: `./start.sh`. Thiếu thư viện thì script hỏi và tự cài.
Để chạy AI thật, đặt `llama-server` cạnh `start.py` (hoặc thêm vào PATH) và để file `.gguf` trong `models/`.
Script mở http://127.0.0.1:8000, ghi log llama-server vào `logs/llama.log`, nhấn Ctrl+C là tắt cả hai.
Trên Colab dùng notebook `Cafe_AI_Colab_Chay_Nhanh.ipynb`.

## Chạy thủ công (không cần model, để xem giao diện)
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
LLM_MOCK=1 uvicorn app.main:app --port 8000            # Windows PowerShell: $env:LLM_MOCK="1"; uvicorn app.main:app --port 8000
```
Mở http://localhost:8000

## Chạy với model thật
1. Cài llama.cpp (https://github.com/ggml-org/llama.cpp/releases, tải bản build sẵn hoặc `brew install llama.cpp`).
2. Tải model (~2 GB): `python -c "from huggingface_hub import hf_hub_download as d; d('Qwen/Qwen2.5-3B-Instruct-GGUF','qwen2.5-3b-instruct-q4_k_m.gguf',local_dir='models')"` (hoặc `huggingface-cli download ...` / `hf download ...` tùy phiên bản)
3. Terminal 1: `./scripts/run_llama.sh`  (Windows: `scripts\run_llama.bat`) – chờ log "server is listening on 127.0.0.1:8080"
4. Terminal 2: `uvicorn app.main:app --port 8000`
5. Mở http://localhost:8000 – chấm xanh góc trên = AI đã kết nối.

## Kiểm thử
```bash
pytest -q                          # 43 test (tests/test_app.py + tests/test_faq.py), không cần model
python scripts/menu_bench.py --check
python scripts/bench_server.py --runs 3     # đo TTFT, token/s qua llama-server đang chạy (dùng được trên Colab)
python scripts/menu_bench.py --model models/qwen2.5-3b-instruct-q4_k_m.gguf --runs 3   # cách khác, cần llama-cpp-python
```

## Cấu trúc
| File | Vai trò |
|---|---|
| `menu.json` | Hallelujah Coffee: 20 món, 8 topping, size M/L, kèm dị ứng (`allergens`), `caffeine`, `bestsellers_seed` |
| `app/menu.py` | nạp menu, **tính tiền** (`price_line`), tạo system prompt |
| `app/cart.py` | giỏ hàng, chốt đơn (ghi `data/orders.jsonl`), hiểu câu lệnh đặt món bằng luật |
| `app/llm.py` | gọi llama-server (OpenAI API, streaming), chế độ mock |
| `app/main.py` | FastAPI: `/api/chat`, `/api/chat/stream` (SSE), `/api/cart/*`, `/api/checkout`, `/api/menu`, `/api/health` |
| `start.py`, `start.bat`, `start.sh` | chạy llama-server + web bằng một lệnh |
| `static/index.html`, `css/`, `js/`, `assets/` | giao diện chat + menu + giỏ hàng (20 ảnh món) |
| `scripts/bench_server.py` | đo tốc độ qua HTTP tới llama-server |
| `scripts/bench_app.py` | đo tải web thật (`/api/chat/stream`): TTFT, RPS, 429, tỷ lệ cache, ghi CSV vào `benchmark/` |
| `benchmark/nguoi2/` | kết quả + notebook + gateway benchmark của Người 2 (V1–V4, 10/20/30 người dùng) |
| `tests/` | pytest + llama-server giả để test streaming |

## Biến môi trường
`LLAMA_URL` (mặc định http://127.0.0.1:8080), `LLM_MOCK`, `LLM_CONCURRENCY` (khớp `--parallel`), `MAX_QUEUE` (mặc định 50 → 429), `MAX_TOKENS`, `TEMPERATURE`, `LLM_TIMEOUT`.

## Điểm bàn giao cho các bạn khác
- **Người 2 (Queue/Scaling):** thay đoạn `_slots`/`_inflight` trong `app/main.py` bằng Redis queue; giữ nguyên mã trả 429 và định dạng SSE. Đổi `Store` sang Redis nếu chạy nhiều worker.
- **Người 3 (Load test):** endpoint cần bắn tải là `POST /api/chat/stream`; câu hỏi tư vấn mới đi qua LLM, câu lệnh đặt món xử lý bằng luật (không tốn LLM) – nên trộn cả hai trong `locustfile`.

## Chatbot trả lời thế nào (3 tầng) và phối hợp với Người 2
Mỗi tin nhắn đi qua lần lượt (app/main.py `_route`):
1. **Luật giỏ hàng** (`app/cart.py`): đặt món, bỏ món, chốt đơn. Source `rules`.
2. **Trả lời bằng code** (`app/faq.py`): giá, tổng tiền, dị ứng, caffeine, lọc theo giá/nhóm (kể cả phủ định: “không uống được cà phê”),
   topping, bán chạy, chào hỏi, thông tin quán. Nhanh, đúng 100% theo `menu.json`, không tốn lượt LLM. Source `faq` / `chitchat` / `info`.
   Thông tin quán (giờ mở cửa, địa chỉ, wifi, ship, đổi trả...) chưa có thì code nói thật “chưa có thông tin”; muốn trả lời thật thì thêm vào
   `menu.json` khối `"shop_info": {"giờ mở cửa": "7h - 22h hằng ngày", "wifi": "Tên: ... / Mật khẩu: ..."}` (khóa = nhãn trong `SHOP_INFO` của faq.py).
   **Câu ngoài lề không bị chặn nữa**: đi qua LLM với `GENERAL_PROMPT` (app/menu.py `build_general_prompt`) - trả lời đúng câu hỏi, nhưng
   không đoán số liệu thời gian thực (giá vàng, thời tiết...) vì model local không có công cụ tra cứu.
3. **LLM** (llama-server): chỉ còn tư vấn mở ("gợi ý món mát", "nên uống A hay B"). Nhận thêm danh sách món đã lọc đúng điều kiện
   (vd "dưới 40k") để không gợi ý sai. Câu độc lập không gửi lịch sử chat (nhanh hơn và cache được); câu nối tiếp ("còn món nào nữa?") thì có.

**Hàng đợi 429 + cache** (`app/shared.py`) dùng cùng tên khóa Redis với gateway của Người 2 (`queue:depth`, `cache:*`, `stat:hit|miss|rejected`):
- `python start.py --redis-url redis://127.0.0.1:6379/0` để dùng Redis; không đặt (hoặc Redis lỗi) thì tự dùng RAM, đúng với 1 worker.
- `--parallel 4 --ctx 16384` là cấu hình V3/V4 của Người 2; `--max-queue N` đặt ngưỡng 429; `--no-cache` khi benchmark V1-V3.
- Chỉ cache câu trả lời LLM của câu độc lập và **không** chứa số tiền lạ (model hay tự cộng sai). Xem số liệu tại `/api/stats`.
- Định dạng SSE (`token`/`done`/`error`) và 429 bằng status code thật giữ nguyên nên giao diện không phải sửa. `source` có thêm các giá trị `faq`, `chitchat`, `info`, `offtopic`, `cache`.

**Cải thiện chatbot:** thêm câu sai vào `tests/eval_questions.json` rồi chạy `python scripts/eval_chat.py --offline` (phần code, không cần model)
hoặc `python scripts/eval_chat.py --url http://127.0.0.1:8000` (chạy thật qua API).

## Kết quả benchmark của Người 2 và cách ghép với app này
Thư mục `benchmark/nguoi2/` giữ nguyên sản phẩm của Người 2 để làm số liệu báo cáo:
`ket_qua_V1_V4.csv`, `bang_so_sanh_20_users.csv`, `ty_le_duoi_3s.csv`, `v4_hit_vs_miss.csv`, `backpressure_429.csv`, `luong_tu_hoa.csv`, 5 biểu đồ `bieu_do_*.png`,
`nguoi2_benchmark_notebook.ipynb` (chạy lại toàn bộ trên Colab), `gateway_benchmark.py` và `system_prompt_benchmark.txt`.

**`gateway_benchmark.py` là bản gốc nguyên văn của Người 2** (để đối chiếu và chạy lại benchmark của họ). Web thật dùng `gateway/gateway.py`: chính gateway đó, đã sửa tối thiểu,
mọi chỗ sửa có chú thích `# [SỬA]`; so sánh từng dòng ở `benchmark/nguoi2/gateway_sua_doi.diff`.

Lưu ý khi đọc số liệu của Người 2:
- Họ đo với system prompt ngắn hơn (`system_prompt_benchmark.txt`, ~3.200 ký tự) và `temperature=0`, `max_tokens=120`. Web thật dùng prompt dài hơn
  (có quy tắc + ví dụ + topping theo nhóm, ~3.600 ký tự), `temperature=0.2`, `max_tokens=256`. TTFT gần như không đổi, nhưng thời gian trả lời trọn vẹn
  có thể dài hơn. Muốn so sánh công bằng: chạy web với `MAX_TOKENS=120 TEMPERATURE=0`.
- Bộ câu hỏi của họ gồm 8 câu FAQ + 60 câu "Cho mình N <món> size X, tổng bao nhiêu tiền?". Trong web thật, **gần như tất cả các câu này được tầng luật/FAQ
  trả lời bằng code, không chạm LLM**. Vì vậy `scripts/bench_app.py` mặc định dùng câu tư vấn mở (đi vào LLM thật) để đo đúng hàng đợi + cache + llama-server.
- "Cache hit 95,5%" của V4 là do bộ test chỉ có ~68 câu lặp lại. Số đáng dẫn trong báo cáo là TTFT của **cache miss** ở `v4_hit_vs_miss.csv`
  (10 người: p50 585 ms; 20 người: 2.478 ms; 30 người: 6.663 ms), vì ngoài thực tế câu hỏi lặp lại ít hơn nhiều.

Đo lại trên web thật (sau khi chạy cell 5 của notebook Colab, hoặc `python start.py ...`):
```
python scripts/bench_app.py --users 10 20 30 --duration 60 --label V4              # --parallel 4, có cache
python scripts/bench_app.py --users 10 20 30 --duration 60 --label V3              # khởi động lại start.py với --no-cache
python scripts/bench_app.py --users 40 --duration 40 --label bp429                 # thêm --max-queue 10 khi start.py để thấy 429
```

## Gateway của Người 2 chạy trước llama-server (`--gateway`)
```
trình duyệt -> web (app/main.py, cổng 8000) -> gateway (gateway/gateway.py, cổng 8082) -> llama-server (cổng 8081)
                 giỏ hàng + luật + FAQ           hàng đợi + 429 + cache + thống kê            model
```
`python start.py --gateway --parallel 4 --ctx 16384 --redis-url redis://127.0.0.1:6379/0 ...` bật cả ba. Web gọi gateway qua `POST /chat` (SSE `{"t": ...}`), đúng giao diện của Người 2.

**Gateway của Người 2 giữ nguyên:** `POST /chat`, `GET /health`, biến `LLAMA_URL`/`VERSION`/`MAX_QUEUE`/`PARALLEL`/`USE_CACHE`/`USE_STREAM`, semaphore, hàng chờ + 429, cache Redis `cache:*`, `stat:hit|miss`, `max_tokens=120`, `temperature=0` mặc định.

**Phải sửa để web thật dùng được (đều có `# [SỬA]` trong file):**
1. Nhận thêm trường `messages` (prompt + lịch sử + danh sách món đã lọc do web dựng); không có thì chạy y như bản gốc (đọc `SYSTEM_PROMPT_PATH`).
2. Redis lấy từ `REDIS_URL`; không có Redis thì dùng RAM (bản gốc sập nếu Redis chưa bật).
3. Cache chỉ cho câu hỏi 1 lượt, khóa gắn với prompt + chuẩn hóa chữ, bỏ qua câu trả lời sai tiền hoặc bị ngắt giữa chừng.
4. Semaphore giữ suốt lúc stream (bản gốc nhả ngay khi trả `StreamingResponse` nên không giới hạn được gì khi stream); hàng chờ nhả đúng khi khách ngắt.
5. 429 luôn bật (bản gốc chỉ bật khi tên `VERSION` có chữ "backpressure", nên V4 không bao giờ trả 429).
6. `/health` kiểm tra llama-server thật; lỗi llama-server trả thành sự kiện lỗi; thêm `/stats`.

**Phối hợp với web:** khi có `--gateway`, cache nằm ở gateway (web tắt cache của mình, tránh 2 lớp). 429 vẫn do web chặn trước (ngưỡng `--parallel + --max-queue`, status 429 thật);
gateway là lưới an toàn phía sau (ngưỡng cao hơn 10), nếu nó trả 429 thì web báo "quán đang rất đông". Số liệu gateway: `http://127.0.0.1:8082/stats`.
Không có `--gateway` thì mọi thứ chạy như cũ. Test: `tests/test_gateway.py`.
