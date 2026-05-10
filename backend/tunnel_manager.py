"""Manage the privileged pymobiledevice3 tunneld helper process.

The tunneld daemon needs root because it creates a UTUN interface to talk to
the iPhone. Rather than asking the user to open Terminal and run `sudo`, we
elevate via macOS's native authentication dialog using:

    osascript -e 'do shell script "..." with administrator privileges'

The same LocationSpoofer binary is invoked with `--tunneld` to act as the
helper (see main.py), so we ship one bundle and one dependency set.

Shutdown goes through tunneld's built-in /shutdown HTTP endpoint, which
sends SIGINT to itself. The HTTP request is unprivileged and never re-prompts.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Tuple

log = logging.getLogger(__name__)

TUNNELD_URL = "http://127.0.0.1:49151"
LOG_PATH = "/tmp/locationspoofer-tunneld.log"


def is_running(timeout: float = 0.5) -> bool:
    try:
        with urllib.request.urlopen(f"{TUNNELD_URL}/hello", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _helper_command() -> List[str]:
    """Build the argv that re-invokes this binary in --tunneld mode."""
    if getattr(sys, "frozen", False):
        # PyInstaller bundle — sys.executable IS the LocationSpoofer binary
        return [sys.executable, "--tunneld"]
    main_py = Path(__file__).resolve().parent / "main.py"
    return [sys.executable, str(main_py), "--tunneld"]


def _build_applescript(shell_cmd: str) -> str:
    escaped = shell_cmd.replace("\\", "\\\\").replace('"', '\\"')
    return f'do shell script "{escaped}" with administrator privileges'


def start(timeout: float = 30.0) -> Tuple[bool, str]:
    """Launch the tunneld helper as root via osascript admin auth.

    Blocks the caller while macOS shows the password prompt and while we wait
    for tunneld to answer /hello. Returns (ok, user-facing-message).
    """
    if is_running():
        return True, "Tunnel already running."

    argv = _helper_command()
    parts = " ".join(shlex.quote(p) for p in argv)
    # `do shell script with administrator privileges` runs in a non-tty
    # context, so nohup fails ("Inappropriate ioctl for device"). Just
    # background with & and redirect stdio explicitly — without a controlling
    # terminal, no SIGHUP is sent, so the daemon survives osascript exiting.
    shell_cmd = f"{parts} >{LOG_PATH} 2>&1 </dev/null &"
    applescript = _build_applescript(shell_cmd)

    try:
        result = subprocess.run(
            ["osascript", "-e", applescript],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return False, "The admin password prompt timed out."
    except FileNotFoundError:
        return False, "osascript not found — this only works on macOS."

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if "User canceled" in stderr or "User cancelled" in stderr:
            return False, "Cancelled. The tunnel needs admin permission to run."
        return False, f"Couldn't start tunnel. {stderr[:160]}"

    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_running():
            return True, "Tunnel started."
        time.sleep(0.4)
    return False, (
        "Tunnel daemon didn't come online in time. "
        f"Check {LOG_PATH} for details."
    )


def stop(timeout: float = 3.0) -> bool:
    """Ask tunneld to shut itself down via /shutdown. No admin prompt."""
    if not is_running():
        return True
    try:
        urllib.request.urlopen(f"{TUNNELD_URL}/shutdown", timeout=timeout).read()
    except (urllib.error.URLError, OSError):
        # The endpoint sends SIGINT to itself, so the response often gets cut
        # short — that surfaces as a URLError. Not a real failure.
        pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_running():
            return True
        time.sleep(0.2)
    return False
