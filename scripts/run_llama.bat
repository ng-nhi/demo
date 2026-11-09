@echo off
REM Windows: chay llama-server (V0 = 1 luong). Doi --parallel 4 va -c 8192 cho V3.
llama-server.exe -m models\qwen2.5-3b-instruct-q4_k_m.gguf -c 4096 --parallel 1 -t 4 -ngl 0 --host 127.0.0.1 --port 8080
