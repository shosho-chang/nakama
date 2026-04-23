@echo off
REM llama.cpp server — Gemma 4 26B-A4B (Q4_K_M)
REM Kept as fallback / historical reference.
REM Production default (post A/B bench 2026-04-23) → scripts\start_qwen_server.bat
REM RTX 5070 Ti 16GB VRAM + i5-13600K (6 P-cores)
REM OpenAI-compatible API: http://localhost:8080/v1

set MODEL=F:\llama.cpp\models\gemma-4-26B-A4B-it-Q4_K_M.gguf
set PORT=8080
set CTX=32768
set GPU_LAYERS=-1

echo Starting llama.cpp server...
echo Model: %MODEL%
echo Port: %PORT%
echo Context: %CTX% (q8_0 KV, flash-attn)
echo GPU Layers: %GPU_LAYERS% (all)
echo.

F:\llama.cpp\llama-server.exe ^
  -m "%MODEL%" ^
  --host 127.0.0.1 ^
  --port %PORT% ^
  -ngl %GPU_LAYERS% ^
  -c %CTX% ^
  -t 6 ^
  --flash-attn on ^
  -ctk q8_0 ^
  -ctv q8_0 ^
  --jinja

pause
