"""
main.py — FastAPI server + native window for Location Spoofer.

Endpoints:
    GET  /          → serves frontend/index.html
    GET  /status    → iPhone connection info
    POST /spoof     → set fake location  { "lat": float, "lng": float }
    POST /reset     → clear fake location (back to real GPS)
    POST /start-tunnel → opens Terminal to launch the dev tunnel (legacy)
    GET  /healthz   → simple liveness probe

Runs on http://localhost:8765 and shows a native macOS WKWebView window via
pywebview. Set LOCATION_SPOOFER_HEADLESS=1 to skip the window (server-only,
useful for tests). Set LOCATION_SPOOFER_BROWSER=1 to open the system browser
instead of the native window.
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


__version__ = "1.1.0"

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

app = FastAPI(title="Location Spoofer", version=__version__, docs_url=None, redoc_url=None)

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


@app.post("/start-tunnel")
def start_tunnel() -> Dict[str, Any]:
    """Launch the tunneld helper with admin auth (native macOS prompt)."""
    if sys.platform != "darwin":
        return {"ok": False, "message": "This only works on macOS."}
    import tunnel_manager
    ok, message = tunnel_manager.start()
    return {"ok": ok, "message": message}


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


def _run_tunneld() -> None:
    """Run as the privileged tunneld helper (invoked with --tunneld).

    The same .app binary serves two roles: the unprivileged GUI (default) and
    the elevated tunneld daemon (this branch). Bundling one binary keeps the
    PyInstaller build simpler and guarantees the helper has the same dep set.
    """
    from pymobiledevice3.tunneld import TUNNELD_DEFAULT_ADDRESS, TunneldRunner

    host, port = TUNNELD_DEFAULT_ADDRESS
    TunneldRunner.create(host, port)


def _auto_prompt_tunnel_when_needed() -> None:
    """Background thread: if an iOS 17+ device is plugged in and the tunnel
    isn't running, trigger the native admin auth dialog automatically.

    Only fires once per app launch. If the user cancels, they can retry via
    the "Allow access" button in the setup card.
    """
    import tunnel_manager
    from spoofer import get_status

    # Give the user a beat to see the window before we pop the password prompt.
    time.sleep(1.5)

    # Wait up to ~10s for the device to be detected. If still no device after
    # that, do nothing — the user will see the "connect iPhone" step and we'll
    # let them click "Allow access" manually once they plug in.
    for _ in range(20):
        try:
            status = get_status()
        except Exception:
            status = {}
        step = status.get("step")
        if step == "no_tunnel":
            if not tunnel_manager.is_running():
                tunnel_manager.start()
            return
        if step in ("ready", None):  # already running or undetectable
            return
        time.sleep(0.5)


def _wait_for_server(url: str, timeout: float = 8.0) -> None:
    """Poll /healthz until uvicorn binds, so the window doesn't show a blank page."""
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{url}healthz", timeout=0.5).read()
            return
        except Exception:
            time.sleep(0.1)


def _serve(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")


def main() -> None:
    if "--tunneld" in sys.argv:
        _run_tunneld()
        return

    url = f"http://{HOST}:{PORT}/"

    if os.environ.get("LOCATION_SPOOFER_HEADLESS") == "1":
        _serve(HOST, PORT)
        return

    if os.environ.get("LOCATION_SPOOFER_BROWSER") == "1":
        threading.Thread(
            target=lambda: (time.sleep(1.0), webbrowser.open(url)),
            daemon=True,
        ).start()
        _serve(HOST, PORT)
        return

    # Native window path: uvicorn on a thread, pywebview on the main thread
    # (pywebview must own the main thread on macOS for the AppKit run loop).
    threading.Thread(target=_serve, args=(HOST, PORT), daemon=True).start()
    _wait_for_server(url)

    # Kick off the auto-prompt before showing the window so it can fire as
    # soon as a device is detected.
    threading.Thread(target=_auto_prompt_tunnel_when_needed, daemon=True).start()

    import webview  # imported lazily so headless mode doesn't pull in pyobjc

    webview.create_window(
        f"Location Spoofer {__version__}",
        url=url,
        width=1180,
        height=780,
        min_size=(900, 620),
    )
    try:
        webview.start()
    finally:
        # Window closed → ask the privileged tunneld helper to exit. Localhost
        # HTTP, no admin re-prompt.
        try:
            import tunnel_manager
            tunnel_manager.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
