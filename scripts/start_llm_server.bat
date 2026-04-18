@echo off
REM 啟動 llama.cpp server with Gemma 4 26B-A4B (Q4_K_M)
REM RTX 5070 Ti 16GB VRAM — 全部 layer offload 到 GPU
REM OpenAI-compatible API: http://localhost:8080/v1

set MODEL=F:\llama.cpp\models\gemma-4-26B-A4B-it-Q4_K_M.gguf
set PORT=8080
set CTX=4096
set GPU_LAYERS=-1

echo Starting llama.cpp server...
echo Model: %MODEL%
echo Port: %PORT%
echo Context: %CTX%
echo GPU Layers: %GPU_LAYERS% (all)
echo.

F:\llama.cpp\llama-server.exe ^
  -m "%MODEL%" ^
  --host 127.0.0.1 ^
  --port %PORT% ^
  -ngl %GPU_LAYERS% ^
  -c %CTX% ^
  -t 8

pause
