"""Phase 0 / Q4: longitudinal non-interactive token-survival probe (run daily).

Each run: load saved tokens (NO interactive fallback), make one cheap
authenticated call (forces a silent refresh if the access token expired), and
append a JSONL row. Run daily on the VPS via cron. The di_refresh_token TTL is
UNPUBLISHED, so this is the only way to learn the real number:

    first date this logs ok=false  -  the login date  =  non-interactive survival window

    python spike/garmin_token_probe.py        # cron: 30 7 * * *
"""
from __future__ import annotations

import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import TOKENSTORE, login  # noqa: E402

LOG = Path(__file__).resolve().parent / "token_probe.jsonl"


def _jwt_exp(token):
    if not token or str(token).count(".") < 2:
        return None
    payload = str(token).split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload)).get("exp")
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    row = {"ts": datetime.now(timezone.utc).isoformat(), "ok": False}
    try:
        g = login(allow_interactive=False)  # NEVER prompt: a failure IS the signal
        g.get_full_name()                    # cheap call -> triggers refresh if needed
        row["ok"] = True
    except Exception as exc:  # noqa: BLE001
        row["error"] = repr(exc)

    tok = TOKENSTORE / "garmin_tokens.json"
    if tok.exists():
        try:
            data = json.loads(tok.read_text())
            exp = _jwt_exp(data.get("di_token") or data.get("access_token"))
            if exp:
                row["access_token_exp"] = exp
                row["access_token_exp_iso"] = datetime.fromtimestamp(exp, timezone.utc).isoformat()
        except Exception as exc:  # noqa: BLE001
            row["token_read_error"] = repr(exc)

    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False))
    if not row["ok"]:
        print("[probe] a FALSE here = non-interactive token survival ended (re-run garmin_auth_spike.py)", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
