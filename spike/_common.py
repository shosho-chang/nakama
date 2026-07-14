"""Shared helpers for the Zoro-coach Phase 0 Garmin spikes.

THROWAWAY spike code (CLAUDE.md §6 / Phase 0). Lives in spike/, NOT in
production agents/zoro/coach/, and deliberately does NOT touch
requirements.txt / pyproject.toml. Install the spike dep in a throwaway venv:

    python -m venv .venv-spike
    .venv-spike\\Scripts\\activate            # Windows PowerShell
    pip install -r spike/requirements-spike.txt

Requires Python >= 3.12 — garminconnect 0.3.x dropped <3.12 when it replaced
the now-dead `garth` lib with a native DI OAuth client (see spike/README.md).

Credentials come from env vars (nothing hard-coded):
    GARMIN_EMAIL, GARMIN_PASSWORD     # only needed for the FIRST interactive login
    GARMINTOKENS                      # token store dir (default data/garmin)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from garminconnect import Garmin
except ModuleNotFoundError:  # pragma: no cover - spike guard
    sys.exit(
        "garminconnect not installed. It is a Phase 0 spike dep, intentionally "
        "NOT in requirements.txt.\n"
        "  pip install -r spike/requirements-spike.txt   (needs Python >= 3.12)"
    )

# garminconnect 0.3.x treats the token store as a DIRECTORY holding
# garmin_tokens.json (di_token / di_refresh_token / di_client_id). Default to a
# gitignored dir under the nakama data dir so it mirrors the google_calendar
# token pattern (shared/google_calendar.py) and is covered by .gitignore `data/*`.
# NOTE: the v2 plan says `data/garmin_token.json` (single file); the real library
# wants a DIR — corrected to data/garmin/ in the §7 backfill.
TOKENSTORE = Path(
    os.environ.get(
        "GARMINTOKENS",
        str(Path(os.environ.get("NAKAMA_DATA_DIR", "data")) / "garmin"),
    )
)


def login(*, allow_interactive: bool = True) -> Garmin:
    """Resume-first login, mirroring upstream example.py.

    Tries the saved token store first; on failure falls back to a one-time
    credential + MFA login (only when allow_interactive). For the token-survival
    probe, pass allow_interactive=False so a resume failure RAISES (that failure
    is exactly the signal we are measuring).
    """
    TOKENSTORE.mkdir(parents=True, exist_ok=True)
    try:
        g = Garmin()
        g.login(str(TOKENSTORE))
        print(f"[auth] resumed from saved tokens at {TOKENSTORE}")
        return g
    except Exception as exc:  # noqa: BLE001 - any resume failure -> interactive (or raise)
        if not allow_interactive:
            raise
        print(f"[auth] saved-token resume failed ({exc!r}); doing credential login")

    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        sys.exit("Set GARMIN_EMAIL and GARMIN_PASSWORD env vars for the first login.")

    g = Garmin(email=email, password=password, prompt_mfa=lambda: input("MFA code: "))
    g.login(str(TOKENSTORE))  # sets the client tokenstore path; prompts MFA via callback

    # Persist tokens. Verified against installed garminconnect 0.3.6: persistence
    # lives on the inner DI client (g.client.dump(path)); the Garmin object has no
    # .dump / .garth. dump() accepts a dir and writes garmin_tokens.json at 0o600.
    g.client.dump(str(TOKENSTORE))
    print(f"[auth] logged in and saved tokens to {TOKENSTORE}")
    return g
