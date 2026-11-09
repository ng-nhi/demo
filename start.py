#!/usr/bin/env python3
"""Chạy cả hệ thống bằng MỘT lệnh:  llama-server (nếu có) + FastAPI + mở trình duyệt.

    python start.py              # tự tìm llama-server và model; thiếu thì chạy AI giả lập
    python start.py --mock       # ép dùng AI giả lập (không cần model, không cần GPU)
    python start.py --help       # xem các tùy chọn

Windows: bấm đúp start.bat.   Mac/Linux: ./start.sh
Dừng: nhấn Ctrl+C (tắt luôn cả hai tiến trình).
"""
import argparse
import glob
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IS_WIN = os.name == "nt"
DEPS = ["fastapi", "uvicorn[standard]", "httpx", "pydantic"]

try:  # tránh lỗi font tiếng Việt trên console Windows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def say(msg=""):
    print(msg, flush=True)


def http_ok(url, timeout=2):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def port_in_use(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_deps(auto_yes):
    missing = [d for d in DEPS if importlib.util.find_spec(d.split("[")[0]) is None]
    if not missing:
        return
    cmd = [sys.executable, "-m", "pip", "install", *DEPS]
    say(f"Thiếu thư viện Python: {', '.join(missing)}")
    say("Lệnh cài:  " + " ".join(cmd[1:]).replace(sys.executable, "python"))
    if not auto_yes:
        if not sys.stdin.isatty():
            sys.exit("Hãy chạy lệnh trên rồi chạy lại (hoặc thêm --yes để tự cài).")
        if input("Cài ngay? [Y/n] ").strip().lower() in ("n", "no"):
            sys.exit("Đã hủy.")
    if subprocess.call(cmd) != 0:
        sys.exit("Cài thư viện thất bại. Kiểm tra mạng rồi thử lại.")


def find_llama_server(explicit):
    cands = [explicit, os.getenv("LLAMA_SERVER"), shutil.which("llama-server")]
    names = ["llama-server.exe", "llama-server"] if IS_WIN else ["llama-server"]
    for base in (ROOT, ROOT / "bin", ROOT / "llama"):
        cands += [str(base / n) for n in names]
    for c in cands:
        if c and Path(c).is_file():
            return str(Path(c).resolve())
    return None


def find_model(explicit):
    if explicit:
        return explicit if Path(explicit).is_file() else None
    files = sorted(glob.glob(str(ROOT / "models" / "*.gguf")))
    return files[0] if files else None


def tail(path, n=1500):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[-n:]
    except Exception:
        return ""


def stop(procs):
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


def main():
    ap = argparse.ArgumentParser(description="Chạy llama-server + web app bằng một lệnh")
    ap.add_argument("--mock", action="store_true", help="dùng AI giả lập, không cần model")
    ap.add_argument("--port", type=int, default=8000, help="cổng web (mặc định 8000)")
    ap.add_argument("--llm-port", type=int, default=8080, help="cổng llama-server (mặc định 8080)")
    ap.add_argument("--model", help="đường dẫn file .gguf (mặc định: file đầu tiên trong models/)")
    ap.add_argument("--llama-server", help="đường dẫn llama-server (mặc định: tự tìm)")
    ap.add_argument("--parallel", type=int, default=1, help="số request chạy LLM cùng lúc (V0=1)")
    ap.add_argument("--ctx", type=int, help="độ dài ngữ cảnh TỔNG (mặc định 4096 x parallel: prompt menu ~1.500 token + lịch sử + câu trả lời cần ~3.000 token mỗi slot)")
    ap.add_argument("--ngl", type=int, default=0, help="số layer đẩy lên GPU; 0 = CPU, 99 = GPU (Colab T4)")
    ap.add_argument("--threads", type=int, default=min(4, os.cpu_count() or 4))
    ap.add_argument("--no-browser", action="store_true", help="không tự mở trình duyệt")
    ap.add_argument("--yes", action="store_true", help="tự đồng ý cài thư viện còn thiếu")
    ap.add_argument("--wait", type=int, default=180, help="số giây chờ llama-server nạp model")
    ap.add_argument("--redis-url", help="vd redis://127.0.0.1:6379/0 : dùng Redis cho hàng đợi 429 + cache (không đặt = dùng RAM)")
    ap.add_argument("--max-queue", type=int, default=50, help="quá parallel + max-queue request đang chờ/chạy thì trả 429")
    ap.add_argument("--gateway", action="store_true", help="chạy thêm GATEWAY CỦA NGƯỜI 2 (gateway/gateway.py) giữa web và llama-server: hàng đợi + 429 + cache nằm ở gateway")
    ap.add_argument("--gateway-port", type=int, default=8082, help="cổng gateway (mặc định 8082)")
    ap.add_argument("--no-cache", action="store_true", help="tắt cache câu trả lời LLM (dùng khi benchmark V1-V3)")
    a = ap.parse_args()

    os.chdir(ROOT)
    if not (ROOT / "app" / "main.py").is_file():
        sys.exit("Không thấy app/main.py. Hãy chạy start.py từ thư mục dự án.")
    ensure_deps(a.yes)

    llm_url = f"http://127.0.0.1:{a.llm_port}"
    web_url = f"http://127.0.0.1:{a.port}"
    procs = []
    mock = a.mock

    # ---- 1) llama-server ----
    if not mock:
        if http_ok(llm_url + "/health"):
            say(f"[AI] llama-server đã chạy sẵn ở {llm_url}, dùng lại.")
        else:
            exe, model = find_llama_server(a.llama_server), find_model(a.model)
            if not exe or not model:
                say("[AI] Chưa đủ để chạy AI thật, chuyển sang AI GIẢ LẬP:")
                if not exe:
                    say("     - chưa thấy llama-server (đặt cạnh start.py, hoặc thêm vào PATH, hoặc dùng --llama-server)")
                if not model:
                    say("     - chưa thấy model .gguf trong thư mục models/ (hoặc dùng --model)")
                mock = True
            else:
                ctx = a.ctx or 4096 * a.parallel
                (ROOT / "logs").mkdir(exist_ok=True)
                log_path = ROOT / "logs" / "llama.log"
                cmd = [exe, "-m", model, "-c", str(ctx), "--parallel", str(a.parallel),
                       "-t", str(a.threads), "-ngl", str(a.ngl),
                       "--host", "127.0.0.1", "--port", str(a.llm_port)]
                say(f"[AI] Bật llama-server ({Path(model).name}, {'GPU' if a.ngl else 'CPU'}), nạp model có thể mất vài chục giây...")
                llama = subprocess.Popen(cmd, stdout=open(log_path, "w"), stderr=subprocess.STDOUT)
                procs.append(llama)
                deadline = time.time() + a.wait
                while time.time() < deadline:
                    if llama.poll() is not None:
                        stop(procs)
                        sys.exit(f"llama-server dừng đột ngột. Log ({log_path}):\n{tail(log_path)}")
                    if http_ok(llm_url + "/health"):
                        say("[AI] llama-server sẵn sàng.")
                        break
                    time.sleep(2)
                else:
                    stop(procs)
                    sys.exit(f"Quá {a.wait}s mà llama-server chưa sẵn sàng. Log ({log_path}):\n{tail(log_path)}")

    # ---- 1b) gateway (tùy chọn): web -> gateway -> llama-server ----
    web_llm_url, gw_url = llm_url, f"http://127.0.0.1:{a.gateway_port}"
    if a.gateway and not mock:
        if port_in_use(a.gateway_port):
            stop(procs)
            sys.exit(f"Cổng gateway {a.gateway_port} đang được dùng. Tắt nó hoặc dùng --gateway-port khác.")
        genv = {**os.environ, "LLAMA_URL": llm_url, "PARALLEL": str(a.parallel), "PYTHONIOENCODING": "utf-8",
                "VERSION": "V3" if a.no_cache else "V4", "USE_STREAM": "1",
                "MAX_QUEUE": str(a.max_queue + 10),   # web chặn 429 trước (parallel + max_queue); gateway chỉ là lưới an toàn phía sau
                "USE_CACHE": "0" if a.no_cache else "1"}
        if a.redis_url:
            genv["REDIS_URL"] = a.redis_url
        say(f"[GATEWAY] Bật gateway ở {gw_url} (-> {llm_url}) ...")
        gw = subprocess.Popen([sys.executable, "-m", "uvicorn", "gateway.gateway:app", "--host", "127.0.0.1",
                               "--port", str(a.gateway_port), "--log-level", "warning"], env=genv)
        procs.append(gw)
        for _ in range(40):
            if gw.poll() is not None:
                stop(procs)
                sys.exit("Gateway dừng đột ngột (xem lỗi phía trên).")
            if http_ok(gw_url + "/health"):
                break
            time.sleep(0.5)
        else:
            stop(procs)
            sys.exit("Gateway không phản hồi sau 20s.")
        web_llm_url = gw_url

    # ---- 2) web app ----
    if port_in_use(a.port):
        stop(procs)
        sys.exit(f"Cổng {a.port} đang được dùng (có thể bản cũ chưa tắt). Tắt nó hoặc dùng --port khác.")
    use_gw = web_llm_url != llm_url  # có gateway thì cache do gateway giữ, web không cache nữa (tránh cache 2 lớp)
    env = {**os.environ, "LLAMA_URL": web_llm_url, "LLM_CONCURRENCY": str(a.parallel),
           "LLM_MOCK": "1" if mock else "0", "PYTHONIOENCODING": "utf-8",
           "MAX_QUEUE": str(a.max_queue), "USE_CACHE": "0" if (a.no_cache or use_gw) else "1",
           "LLM_GATEWAY_MODE": "chat" if use_gw else ""}
    if a.redis_url:
        env["REDIS_URL"] = a.redis_url
    say(f"[WEB] Bật FastAPI ở {web_url} ...")
    web = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app",
                            "--host", "127.0.0.1", "--port", str(a.port)], env=env)
    procs.append(web)
    for _ in range(40):
        if web.poll() is not None:
            stop(procs)
            sys.exit("Web app dừng đột ngột (xem lỗi phía trên).")
        if http_ok(web_url + "/api/health"):
            break
        time.sleep(0.5)
    else:
        stop(procs)
        sys.exit("Web app không phản hồi sau 20s.")

    say("")
    say("=" * 60)
    say(f"  Mở web:        {web_url}")
    say(f"  Thử API:       {web_url}/docs   (số liệu hàng đợi/cache: {web_url}/api/stats)")
    say(f"  Chế độ AI:     {'GIẢ LẬP (câu tư vấn trả lời mẫu)' if mock else 'THẬT (llama-server ' + llm_url + ')'}")
    if use_gw:
        say(f"  Gateway:       {gw_url}   (số liệu: {gw_url}/stats)")
    say("  Dừng:          nhấn Ctrl+C")
    say("=" * 60)
    if not a.no_browser:
        webbrowser.open(web_url)

    try:
        while True:
            for p in procs:
                if p.poll() is not None:
                    say("Một tiến trình đã dừng, đang tắt phần còn lại...")
                    raise KeyboardInterrupt
            time.sleep(1)
    except KeyboardInterrupt:
        say("\nĐang tắt...")
    finally:
        stop(procs)
        say("Đã tắt.")


if __name__ == "__main__":
    main()
