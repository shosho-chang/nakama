"""驗 _download_result + _trim_jingle 在現有 production 上可以跑通。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from shared.auphonic import (  # noqa: E402
    _API_BASE,
    _download_result,
    _headers,
    _load_accounts,
    _trim_jingle,
)

UUID = "pZjSkBoV6kYapYmKPZDQbF"
account = _load_accounts()[3]

resp = httpx.get(
    f"{_API_BASE}/production/{UUID}.json",
    headers=_headers(account.api_key),
    timeout=30,
)
resp.raise_for_status()
data = resp.json()["data"]

output_path = Path("tests/files/out/angie-test_normalized.wav")
output_path.parent.mkdir(parents=True, exist_ok=True)

downloaded = _download_result(account.api_key, data, output_path)
print(f"Downloaded: {downloaded} ({downloaded.stat().st_size / 1e6:.1f} MB)")

trimmed = _trim_jingle(downloaded, jingle_seconds=6.0)
print(f"Trimmed: {trimmed} ({trimmed.stat().st_size / 1e6:.1f} MB)")
