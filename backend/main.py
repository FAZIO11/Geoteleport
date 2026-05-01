"""
main.py — FastAPI server that wraps spoofer.py and serves the map UI.

Endpoints:
    GET  /          → serves frontend/index.html
    GET  /status    → iPhone connection info
    POST /spoof     → set fake location  { "lat": float, "lng": float }
    POST /reset     → clear fake location (back to real GPS)
    GET  /healthz   → simple liveness probe

Runs on http://localhost:8765 by default. When launched directly (or as the
PyInstaller .app), it also opens the user's browser to the map UI.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError

# Allow running both as `python main.py` (script) and as a packaged app where
# the module path may differ. Add this file's folder to sys.path so the import
# works in both modes.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from spoofer import clear_location, get_status, spoof_location  # noqa: E402


HOST = "127.0.0.1"
PORT = 8765


# --------------------------------------------------------------------------- #
# Frontend file lookup (works in dev + PyInstaller bundle)
# --------------------------------------------------------------------------- #


def _frontend_path() -> Path:
    """Find index.html in dev mode and inside a PyInstaller --add-data bundle."""
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return Path(bundle_dir) / "frontend" / "index.html"
    # Dev mode: backend/main.py → ../frontend/index.html
    return Path(__file__).resolve().parent.parent / "frontend" / "index.html"


# --------------------------------------------------------------------------- #
# App + middleware
# --------------------------------------------------------------------------- #

app = FastAPI(title="Location Spoofer", version="1.0.0", docs_url=None, redoc_url=None)

# Permissive CORS — this server only ever binds to localhost, and we want the
# bundled HTML file to be able to call it whether opened via http:// or file://.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class SpoofRequest(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0, description="Latitude (-90 to 90)")
    lng: float = Field(..., ge=-180.0, le=180.0, description="Longitude (-180 to 180)")


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@app.get("/", include_in_schema=False)
def serve_index():
    path = _frontend_path()
    if not path.exists():
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "message": "The map UI file is missing. The app bundle is incomplete.",
            },
        )
    return FileResponse(path, media_type="text/html")


@app.get("/healthz", include_in_schema=False)
def healthz() -> Dict[str, bool]:
    return {"ok": True}


@app.get("/status")
def status() -> Dict[str, Any]:
    try:
        return get_status()
    except Exception as exc:  # last-ditch safety net
        return {
            "connected": False,
            "device_name": None,
            "ios_version": None,
            "model": None,
            "needs_tunnel": False,
            "message": f"Something went wrong checking the iPhone. ({str(exc)[:120]})",
        }


@app.post("/spoof")
def spoof(req: SpoofRequest) -> Dict[str, Any]:
    try:
        result = spoof_location(req.lat, req.lng)
        return {"ok": result.ok, "message": result.message}
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Unexpected error while changing location. ({str(exc)[:120]})",
        }


@app.post("/reset")
def reset() -> Dict[str, Any]:
    try:
        result = clear_location()
        return {"ok": result.ok, "message": result.message}
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Unexpected error while resetting location. ({str(exc)[:120]})",
        }


# Make Pydantic validation errors look human instead of FastAPI's default dump.
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi import Request  # noqa: E402


@app.exception_handler(RequestValidationError)
async def _human_validation_error(_: Request, exc: RequestValidationError):
    # Most common case: lat/lng out of range or missing
    return JSONResponse(
        status_code=400,
        content={
            "ok": False,
            "message": "Latitude must be between -90 and 90, longitude between -180 and 180.",
        },
    )


# --------------------------------------------------------------------------- #
# Entry point — also opens the browser when launched standalone
# --------------------------------------------------------------------------- #


def _open_browser_when_ready(url: str) -> None:
    # Tiny delay so uvicorn has bound the port before we hit it.
    time.sleep(1.0)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main() -> None:
    import uvicorn

    url = f"http://{HOST}:{PORT}/"
    if os.environ.get("LOCATION_SPOOFER_NO_BROWSER") != "1":
        threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
