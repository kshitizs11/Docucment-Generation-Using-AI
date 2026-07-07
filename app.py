#!/usr/bin/env python3
"""
Single entry point for the whole service: `python app.py`

Equivalent to `cd backend && uvicorn app.main:app --reload`, just without needing to
know that the actual application lives under backend/app/.
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent / "backend"
ENV_FILE = BACKEND_DIR / ".env"
ENV_EXAMPLE = BACKEND_DIR / ".env.example"


def _check_env() -> None:
    if not ENV_FILE.exists():
        print(f"No {ENV_FILE} found.", flush=True)
        print(f"Run: cp {ENV_EXAMPLE} {ENV_FILE}", flush=True)
        print("Then edit it and paste in a real ANTHROPIC_API_KEY before generating anything.", flush=True)
        print("Starting anyway -- the server will run, but /generate will fail until that's set.\n", flush=True)
        return
    if "REPLACE_ME" in ENV_FILE.read_text():
        print(f"{ENV_FILE} still has a placeholder ANTHROPIC_API_KEY.", flush=True)
        print("Starting anyway -- the server will run, but /generate will fail until that's set.\n", flush=True)


def main() -> None:
    _check_env()
    sys.path.insert(0, str(BACKEND_DIR))
    import uvicorn

    reload = "--no-reload" not in sys.argv
    print(
        f"Starting AI Document Platform at http://127.0.0.1:8000/ (reload={'on' if reload else 'off'})",
        flush=True,
    )
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=reload, app_dir=str(BACKEND_DIR))


if __name__ == "__main__":
    main()
