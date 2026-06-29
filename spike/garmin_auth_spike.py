"""Phase 0 / Q4: one-time LOCAL Garmin login -> portable token store.

Mirrors scripts/google_calendar_auth.py: run this ONCE on a trusted local
machine (it handles password + MFA interactively), which writes the token store
to data/garmin/ (gitignored), then copy it to the VPS so the headless job can
auto-refresh non-interactively. See spike/README.md for why.

    $env:GARMIN_EMAIL="you@example.com"     # PowerShell
    $env:GARMIN_PASSWORD="..."
    python spike/garmin_auth_spike.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import TOKENSTORE, login  # noqa: E402


def main() -> None:
    g = login(allow_interactive=True)
    try:
        name = g.get_full_name()
        print(f"[ok] authenticated as: {name}")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] auth ok but probe call failed: {exc!r}")

    store = TOKENSTORE.resolve()
    print("\n--- token store written ---")
    for p in sorted(store.glob("*")):
        print(f"  {p}")
    print(
        "\nTo deploy to the VPS (mirrors scripts/google_calendar_auth.py):\n"
        f"  scp -r {store} nakama-vps:/home/nakama/data/garmin\n"
        "  ssh nakama-vps 'chmod 700 /home/nakama/data/garmin && chmod 600 /home/nakama/data/garmin/*'\n"
        "Then set GARMINTOKENS=/home/nakama/data/garmin for the cron job\n"
        "(or rely on NAKAMA_DATA_DIR=data with the store at data/garmin)."
    )


if __name__ == "__main__":
    main()
