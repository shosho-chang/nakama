"""Run the Podcast Carousel review surface without the rest of Thousand Sunny."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thousand_sunny.routers.carousel_review import page_router


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episodes_root", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    os.environ["PODCAST_EPISODES_ROOT"] = str(args.episodes_root.resolve())
    app = FastAPI(docs_url=None, redoc_url=None)
    app.include_router(page_router)
    static_dir = Path(__file__).resolve().parents[1] / "thousand_sunny" / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
