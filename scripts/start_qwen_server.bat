@echo off
REM llama.cpp server — Qwen3.6-35B-A3B (Q4_K_M, unsloth UD)
REM RTX 5070 Ti 16GB VRAM + i5-13600K (6 P-cores)
REM OpenAI-compatible API: http://localhost:8080/v1
REM
REM 注意：
REM - Qwen 3.6 35B-A3B 是 MoE 模型（35B 總參數，每 token 激活 3B）
REM - Q4_K_M GGUF 約 22GB，16GB VRAM 無法全部 offload，需要 partial offload 到 RAM
REM - GPU_LAYERS=35 為建議起始值（總 40 層，留 5 層在 CPU）
REM - 若速度太慢可降到 30，若有 VRAM 空間可升到 38
REM - 只激活 3B，CPU offload 的延遲懲罰小

set MODEL=F:\llama.cpp\models\Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
set PORT=8080
set CTX=32768
set GPU_LAYERS=35

echo Starting llama.cpp server (Qwen3.6-35B-A3B)...
echo Model: %MODEL%
echo Port: %PORT%
echo Context: %CTX% (q8_0 KV, flash-attn)
echo GPU Layers: %GPU_LAYERS% / 40 (partial offload, rest on CPU)
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
  --jinja ^
  --reasoning off ^
  --reasoning-budget 0

pause
