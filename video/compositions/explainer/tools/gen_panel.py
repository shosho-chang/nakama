# 漫畫格的畫面：OpenAI Images API 直接存檔
#   python -I gen_panel.py <out.png> <size> <prompt 檔> [參考圖 ...]
#   size：1536x1024 / 1024x1024 / 1024x1536
#   有參考圖 → /v1/images/edits（角色照參考圖畫）；沒有 → /v1/images/generations
#   環境變數 FIDELITY=low|high（預設 high；只要借畫風時用 low 比較便宜）
# 跟舊的 scratchpad gen_scene.py 不同：風格與鏡頭分開，鏡頭（景別、角度）由每格的 prompt 自己寫，
# 不再把每張都鎖成「一點透視、平視、地平線 40%」——那是第一版每格都一樣的原因。
# 價格（2026-11-18 OpenAI pricing，每 1M tokens）：chatgpt-image-latest
#   text in $5、image in $8、image out $32
import base64
import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path

PRICE = {"chatgpt-image-latest": (5, 8, 32, 10), "gpt-image-1.5": (5, 8, 32, 10)}
MODEL = "chatgpt-image-latest"

out, size, prompt_file = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
refs = [Path(p) for p in sys.argv[4:]]
prompt = prompt_file.read_text(encoding="utf-8").strip()
key = next(
    line.split("=", 1)[1].strip().strip('"')
    for line in open(r"E:\nakama\.env", encoding="utf-8")
    if line.startswith("OPENAI_API_KEY=")
)

if refs:
    bd = uuid.uuid4().hex
    parts = []

    def field(name, val):
        parts.append(
            f'--{bd}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{val}\r\n'.encode()
        )

    for k, v in {
        "model": MODEL,
        "prompt": prompt,
        "size": size,
        "quality": "medium",
        "n": "1",
        "input_fidelity": os.environ.get("FIDELITY", "high"),
    }.items():
        field(k, v)
    for r in refs:
        parts.append(
            (
                f"--{bd}\r\nContent-Disposition: form-data; "
                f'name="image[]"; filename="{r.name}"\r\nContent-Type: image/png\r\n\r\n'
            ).encode()
            + r.read_bytes()
            + b"\r\n"
        )
    parts.append(f"--{bd}--\r\n".encode())
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/edits",
        data=b"".join(parts),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": f"multipart/form-data; boundary={bd}",
        },
    )
else:
    body = {"model": MODEL, "prompt": prompt, "size": size, "quality": "medium", "n": 1}
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
t0 = time.time()
try:
    d = json.load(urllib.request.urlopen(req, timeout=400))
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode()[:800])
    sys.exit(1)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(base64.b64decode(d["data"][0]["b64_json"]))
u = d.get("usage", {})
det, od = u.get("input_tokens_details", {}), u.get("output_tokens_details", {})
tin, iin, iout, tout = PRICE[MODEL]
cost = (
    det.get("text_tokens", 0) * tin
    + det.get("image_tokens", 0) * iin
    + od.get("image_tokens", u.get("output_tokens", 0)) * iout
    + od.get("text_tokens", 0) * tout
) / 1e6
rec = {
    "file": str(out),
    "model": MODEL,
    "size": size,
    "refs": [r.name for r in refs],
    "secs": round(time.time() - t0, 1),
    "usage": u,
    "usd": round(cost, 4),
}
print(json.dumps(rec, ensure_ascii=False))
with open(out.parent / "_cost_log.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
