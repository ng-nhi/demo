#!/usr/bin/env bash
# Chạy llama-server với Qwen2.5-3B-Instruct Q4_K_M (V0: 1 luồng xử lý).
# Tải model 1 lần:  huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF qwen2.5-3b-instruct-q4_k_m.gguf --local-dir models
MODEL=${MODEL:-models/qwen2.5-3b-instruct-q4_k_m.gguf}
PARALLEL=${PARALLEL:-1}      # V3 của nhóm: đổi thành 4
CTX=${CTX:-4096}             # LƯU Ý: tổng ngữ cảnh chia đều cho các slot (4096/4 = 1024/slot -> nên tăng CTX = 2048*PARALLEL)
THREADS=${THREADS:-4}
NGL=${NGL:-0}                # số layer đẩy lên GPU, 0 = chạy CPU (có GPU như Colab T4: NGL=99)
llama-server -m "$MODEL" -c "$CTX" --parallel "$PARALLEL" -t "$THREADS" -ngl "$NGL" --host 127.0.0.1 --port 8080
