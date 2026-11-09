#!/usr/bin/env bash
# LLM_MOCK=1 ./scripts/run_web.sh   -> chạy không cần model (dev giao diện)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
