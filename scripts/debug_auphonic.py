"""對 Auphonic /productions.json 發一次 request，印出 400 的完整 response body。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from shared.auphonic import _load_accounts, _load_env_defaults  # noqa: E402

account = _load_accounts()[3]  # bonvoyage — pipeline 選到的
params = _load_env_defaults()

algorithms = {
    "normloudness": True,
    "loudnesstarget": str(int(params["loudness_target"])),
    "loudnessmethod": params["loudness_method"],
    "denoise": params["denoise"],
    "denoisemethod": params["denoise_method"],
    "denoiseamount": params["denoise_amount"],
    "deverbamount": params["deverb_amount"],
    "debreathamount": params["debreath_amount"],
    "leveler": params["leveler"],
    "levelerstrength": params["leveler_strength"],
    "compressor": params["compressor"],
    "filtering": params["filtering"],
    "filtermethod": params["filter_method"],
    "silence_cutter": params["silence_cutter"],
    "filler_cutter": params["filler_cutter"],
}
if params["max_peak"] != "auto":
    algorithms["maxpeak"] = params["max_peak"]

output_file = {"format": params["output_format"], "bitdepth": params["output_bitdepth"]}

payload = {"output_files": [output_file], "algorithms": algorithms}
print("Payload:")
print(json.dumps(payload, indent=2, ensure_ascii=False))
print()

resp = httpx.post(
    "https://auphonic.com/api/productions.json",
    json=payload,
    headers={
        "Authorization": f"bearer {account.api_key}",
        "Content-Type": "application/json",
    },
    timeout=30,
)
print(f"Status: {resp.status_code}")
print("Response body:")
print(resp.text)
