"""
spoofer.py — iPhone GPS location spoofing core.

Talks to a USB-connected iPhone via pymobiledevice3 and injects a fake
GPS coordinate. Returns plain-English Result objects so callers (CLI,
FastAPI) never have to deal with raw tracebacks.

Key design note (iOS 17+ / iOS < 17):
  LocationSimulation via DVT only persists while the DVT socket is open.
  We keep that socket alive in a daemon thread (_SESSION_THREAD) and signal
  it to close when the user resets. For iOS < 17 we first try the simpler
  DtSimulateLocation service (fire-and-forget, truly persistent), falling
  back to the DVT keepalive if that service is unavailable.

Usage from the command line:
    python spoofer.py <lat> <lng>
    python spoofer.py reset
    python spoofer.py status
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import logging
import sys
import threading
from dataclasses import dataclass, asdict
from typing import Any, Callable, Dict, List, Optional

from pymobiledevice3 import exceptions as pmd_exc
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.dvt.dvt_secure_socket_proxy import (
    DvtSecureSocketProxyService,
)
from pymobiledevice3.services.dvt.instruments.location_simulation import (
    LocationSimulation,
)
from pymobiledevice3.services.simulate_location import DtSimulateLocation


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

log = logging.getLogger("location_spoofer")
if not log.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("[spoofer] %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False


# --------------------------------------------------------------------------- #
# Timeouts (seconds)
# --------------------------------------------------------------------------- #

CONNECT_TIMEOUT = 8.0
TUNNELD_TIMEOUT = 4.0
OPERATION_TIMEOUT = 18.0


def _run_with_timeout(fn: Callable, timeout: float, label: str = "operation"):
    """
    Run a blocking sync function in a daemon thread, return its result, or
    raise TimeoutError if it doesn't finish in time.
    """
    fut: concurrent.futures.Future = concurrent.futures.Future()

    def _runner():
        try:
            fut.set_result(fn())
        except BaseException as exc:  # noqa: BLE001
            fut.set_exception(exc)

    threading.Thread(target=_runner, daemon=True, name=f"spoofer-{label}").start()
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        raise TimeoutError(f"{label} took longer than {timeout:.0f}s")


# --------------------------------------------------------------------------- #
# Result type
# --------------------------------------------------------------------------- #


@dataclass
class Result:
    """Plain success/failure carrier with a human-readable message."""

    ok: bool
    message: str

    def __str__(self) -> str:  # pragma: no cover
        prefix = "✓" if self.ok else "✗"
        return f"{prefix} {self.message}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _short_error(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    return text[:140]


def _valid_coords(lat: float, lng: float) -> bool:
    return -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0


def _major_ios_version(version: Optional[str]) -> int:
    if not version:
        return 0
    try:
        return int(version.split(".")[0])
    except (ValueError, AttributeError):
        return 0


def _connect_lockdown(autopair: bool = True):
    try:
        return _run_with_timeout(
            lambda: create_using_usbmux(autopair=autopair),
            timeout=CONNECT_TIMEOUT,
            label="usbmux-connect",
        )
    except TimeoutError:
        raise RuntimeError(
            "Couldn't reach the iPhone in time. Unplug, replug the cable, "
            "and try again. If the phone is locked, unlock it first."
        )
    except pmd_exc.NoDeviceConnectedError:
        raise RuntimeError(
            "No iPhone detected. Make sure your cable is connected and tap Trust on your phone."
        )
    except pmd_exc.PasswordRequiredError:
        raise RuntimeError(
            "Your iPhone is locked. Unlock it, tap Trust on the popup, and try again."
        )
    except pmd_exc.UserDeniedPairingError:
        raise RuntimeError(
            "Pairing was denied on your iPhone. Unplug, replug, and tap Trust this time."
        )
    except (ConnectionError, OSError) as exc:
        raise RuntimeError(
            f"Couldn't talk to your iPhone over USB. Try a different cable or USB port. ({_short_error(exc)})"
        )


def _device_label(lockdown) -> str:
    info = getattr(lockdown, "short_info", None) or {}
    return info.get("DeviceName") or "your iPhone"


def _get_label_from_rsd(rsd) -> str:
    """Best-effort device name from a RemoteServiceDiscoveryService object."""
    try:
        name = rsd.lockdown.get_value("", "DeviceName")
        if name:
            return name
    except Exception:
        pass
    return getattr(rsd, "product_type", None) or "iPhone"


def _connection_type_from_tunneld() -> Optional[str]:
    """
    Inspect the tunneld HTTP response to determine whether the active tunnel
    is USB or Wi-Fi.  Interface names from the usbmux monitor start with
    'usbmux-'; anything else (bare IPv6/IPv4 from the Wi-Fi monitor) is Wi-Fi.
    Returns 'usb', 'wifi', or None if tunneld is unreachable.
    """
    try:
        import requests as _req
        tunnels: dict = _req.get("http://127.0.0.1:49151", timeout=1).json()
    except Exception:
        return None
    for details in tunnels.values():
        for entry in details:
            if not entry.get("interface", "").startswith("usbmux-"):
                return "wifi"
    return "usb"


# --------------------------------------------------------------------------- #
# Persistent DVT session — keeps the location alive until explicitly stopped
# --------------------------------------------------------------------------- #
# DVT LocationSimulation only persists while the DVT socket is open.
# We park a daemon thread with that socket open and signal it to close on reset.

_SESSION_LOCK = threading.Lock()
_SESSION_STOP: Optional[threading.Event] = None
_SESSION_THREAD: Optional[threading.Thread] = None


def _stop_active_session() -> None:
    """Signal + join the current keepalive thread. Must be called with _SESSION_LOCK held."""
    global _SESSION_STOP, _SESSION_THREAD
    if _SESSION_STOP is not None:
        _SESSION_STOP.set()
    if _SESSION_THREAD is not None and _SESSION_THREAD.is_alive():
        _SESSION_THREAD.join(timeout=8)
    _SESSION_STOP = None
    _SESSION_THREAD = None


def _start_dvt_keepalive(lockdown_provider, lat: float, lng: float) -> tuple[threading.Event, List[Optional[BaseException]], threading.Event]:
    """
    Launch a background thread that:
      1. Opens a DVT connection
      2. Sets the location
      3. Signals set_event so the caller knows it worked
      4. Blocks on stop_event (keeping the DVT socket—and location—alive)
      5. Clears the location and exits when stop_event fires

    Returns (set_event, error_holder, stop_event).
    """
    global _SESSION_STOP, _SESSION_THREAD

    set_event = threading.Event()
    error_holder: List[Optional[BaseException]] = [None]
    stop_event = threading.Event()

    def _keepalive():
        try:
            with DvtSecureSocketProxyService(lockdown=lockdown_provider) as dvt:
                LocationSimulation(dvt).set(lat, lng)
                log.info("DVT keepalive: location active, socket held open.")
                set_event.set()
                stop_event.wait()
                with contextlib.suppress(Exception):
                    LocationSimulation(dvt).clear()
                log.info("DVT keepalive: location cleared, thread exiting.")
        except Exception as exc:
            log.warning("DVT keepalive error: %s", _short_error(exc))
            error_holder[0] = exc
        finally:
            set_event.set()  # always unblock the caller, even on error or early stop

    with _SESSION_LOCK:
        _stop_active_session()
        _SESSION_STOP = stop_event
        _SESSION_THREAD = threading.Thread(target=_keepalive, daemon=True, name="dvt-keepalive")
        _SESSION_THREAD.start()

    return set_event, error_holder, stop_event


def _wait_for_dvt_set(set_event: threading.Event, error_holder: List[Optional[BaseException]], label: str) -> Optional[Result]:
    """
    Wait for the keepalive thread to confirm the location is set.
    Returns a failure Result if something went wrong, or None on success.
    """
    if not set_event.wait(timeout=OPERATION_TIMEOUT):
        with _SESSION_LOCK:
            _stop_active_session()
        return Result(
            False,
            f"Timed out setting location on {label}. "
            "Check that the tunnel (start-tunnel.sh) is running and Developer Mode is on, then try again.",
        )

    if error_holder[0] is not None:
        with _SESSION_LOCK:
            _stop_active_session()
        return Result(
            False,
            f"Couldn't set location on {label}. ({_short_error(error_holder[0])})",
        )

    return None  # success


# --------------------------------------------------------------------------- #
# iOS 17+ path (RemoteServiceDiscovery via tunneld)
# --------------------------------------------------------------------------- #


def _ios17_unsupported_message() -> str:
    return (
        "Your iPhone is on iOS 17 or newer. iOS 17+ needs an extra setup step "
        "(developer tunnel). Open Terminal and run:\n"
        "    ./backend/start-tunnel.sh\n"
        "Leave that running, then come back and try again. See the README for details."
    )


def _get_ios17_rsd():
    """
    Try to grab a RemoteServiceDiscoveryService for an iOS 17+ device via the
    tunneld helper daemon. Returns the RSD object or None if nothing is
    available. Never raises and never blocks longer than TUNNELD_TIMEOUT + 2s.

    Always runs the async query in a brand-new thread with its own event loop
    so we never collide with FastAPI/uvicorn's running event loop.
    """
    try:
        try:
            from pymobiledevice3.tunneld import async_get_tunneld_devices  # type: ignore
        except ImportError:
            from pymobiledevice3.remote.tunneld import (  # type: ignore
                async_get_tunneld_devices,
            )
    except ImportError:
        log.warning("tunneld module not available in this pymobiledevice3 install")
        return None

    result_holder: List[Any] = [None]
    error_holder: List[Optional[BaseException]] = [None]

    def _in_thread():
        try:
            # asyncio.run() creates a fresh event loop — safe even when called
            # from a FastAPI worker thread that already has a loop running.
            result_holder[0] = asyncio.run(
                asyncio.wait_for(async_get_tunneld_devices(), timeout=TUNNELD_TIMEOUT)
            )
        except Exception as exc:
            error_holder[0] = exc

    t = threading.Thread(target=_in_thread, daemon=True, name="tunneld-query")
    t.start()
    t.join(timeout=TUNNELD_TIMEOUT + 2)

    if t.is_alive():
        log.warning("tunneld query thread still running after timeout — tunneld may be down")
        return None

    if error_holder[0] is not None:
        log.warning("tunneld query failed: %s", _short_error(error_holder[0]))
        return None

    devices = result_holder[0]
    if not devices:
        log.warning("tunneld is up but reports zero devices — check Developer Mode on the iPhone")
        return None

    return devices[0]


def _spoof_ios17(lat: float, lng: float, label: str) -> Result:
    log.info("ios17 path: looking up tunneld device...")
    rsd = _get_ios17_rsd()
    if rsd is None:
        return Result(False, _ios17_unsupported_message())

    log.info("ios17 path: starting DVT keepalive session...")
    set_event, error_holder, _ = _start_dvt_keepalive(rsd, lat, lng)
    failure = _wait_for_dvt_set(set_event, error_holder, label)
    if failure:
        return failure

    log.info("ios17 path: location set and held active.")
    return Result(True, f"Location set on {label}.")


def _clear_ios17(label: str) -> Result:
    with _SESSION_LOCK:
        had_session = _SESSION_STOP is not None
        _stop_active_session()

    if had_session:
        log.info("ios17 clear: stopped DVT keepalive session.")
        return Result(True, f"Location reset on {label}. Real GPS is back.")

    # No active keepalive — send a one-shot clear via a brief DVT connection.
    rsd = _get_ios17_rsd()
    if rsd is not None:
        def _do():
            with DvtSecureSocketProxyService(lockdown=rsd) as dvt:
                LocationSimulation(dvt).clear()
        with contextlib.suppress(Exception):
            _run_with_timeout(_do, timeout=OPERATION_TIMEOUT, label="ios17-clear")

    return Result(True, f"Location reset on {label}. Real GPS is back.")


# --------------------------------------------------------------------------- #
# Classic path (iOS < 17, plain lockdown)
# --------------------------------------------------------------------------- #


def _spoof_classic(lockdown, lat: float, lng: float, label: str) -> Result:
    # Prefer DtSimulateLocation: fire-and-forget, truly persistent, no keepalive needed.
    log.info("classic path: trying DtSimulateLocation (fire-and-forget)...")
    try:
        def _dt():
            DtSimulateLocation(lockdown).set(lat, lng)
        _run_with_timeout(_dt, timeout=OPERATION_TIMEOUT, label="classic-dt-set")
        log.info("classic path: DtSimulateLocation set succeeded.")
        return Result(True, f"Location set on {label}.")
    except Exception as exc:
        log.info("DtSimulateLocation unavailable (%s), falling back to DVT keepalive...", _short_error(exc))

    # Fall back to DVT keepalive (same mechanism as iOS 17+).
    log.info("classic path: starting DVT keepalive session...")
    set_event, error_holder, _ = _start_dvt_keepalive(lockdown, lat, lng)
    failure = _wait_for_dvt_set(set_event, error_holder, label)
    if failure:
        return failure

    log.info("classic path: location set and held active via DVT keepalive.")
    return Result(True, f"Location set on {label}.")


def _clear_classic(lockdown, label: str) -> Result:
    with _SESSION_LOCK:
        had_session = _SESSION_STOP is not None
        _stop_active_session()

    if had_session:
        log.info("classic clear: stopped DVT keepalive session.")
        return Result(True, f"Location reset on {label}. Real GPS is back.")

    # No active keepalive — try DtSimulateLocation clear, then DVT one-shot.
    cleared = False
    try:
        def _dt():
            DtSimulateLocation(lockdown).clear()
        _run_with_timeout(_dt, timeout=OPERATION_TIMEOUT, label="classic-dt-clear")
        cleared = True
    except Exception:
        pass

    if not cleared:
        def _do():
            with DvtSecureSocketProxyService(lockdown=lockdown) as dvt:
                LocationSimulation(dvt).clear()
        with contextlib.suppress(Exception):
            _run_with_timeout(_do, timeout=OPERATION_TIMEOUT, label="classic-clear")

    return Result(True, f"Location reset on {label}. Real GPS is back.")


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def spoof_location(latitude: float, longitude: float) -> Result:
    """Set the iPhone's GPS to the given coordinates."""
    log.info("spoof requested: lat=%.5f lng=%.5f", latitude, longitude)

    if not _valid_coords(latitude, longitude):
        return Result(
            False,
            "Those coordinates don't look right. Latitude must be between -90 and 90, longitude between -180 and 180.",
        )

    try:
        lockdown = _connect_lockdown()
    except RuntimeError as exc:
        log.warning("USB connect failed (%s) — trying Wi-Fi tunnel...", exc)
        rsd = _get_ios17_rsd()
        if rsd is None:
            return Result(
                False,
                "No iPhone found via USB or Wi-Fi. Connect your iPhone or check the tunnel is running.",
            )
        label = _get_label_from_rsd(rsd)
        log.info("device via Wi-Fi: %s, iOS %s", label, rsd.product_version)
        return _spoof_ios17(latitude, longitude, label)

    label = _device_label(lockdown)
    ios_version = getattr(lockdown, "product_version", None)
    major = _major_ios_version(ios_version)
    log.info("device: %s, iOS %s", label, ios_version)

    if major >= 17:
        return _spoof_ios17(latitude, longitude, label)
    return _spoof_classic(lockdown, latitude, longitude, label)


def clear_location() -> Result:
    """Stop spoofing — restore the iPhone's real GPS."""
    try:
        lockdown = _connect_lockdown()
    except RuntimeError as exc:
        log.warning("USB connect failed (%s) — trying Wi-Fi tunnel for clear...", exc)
        rsd = _get_ios17_rsd()
        if rsd is None:
            # No USB, no Wi-Fi — if a DVT session is active locally we can still stop it
            with _SESSION_LOCK:
                had = _SESSION_STOP is not None
                _stop_active_session()
            if had:
                return Result(True, "Location reset. Real GPS is back.")
            return Result(False, "No iPhone found via USB or Wi-Fi.")
        return _clear_ios17(_get_label_from_rsd(rsd))

    label = _device_label(lockdown)
    major = _major_ios_version(getattr(lockdown, "product_version", None))

    if major >= 17:
        return _clear_ios17(label)
    return _clear_classic(lockdown, label)


def _tunneld_is_running() -> bool:
    """Quick HTTP ping to the tunneld daemon — no iPhone needed."""
    try:
        import requests as _req
        _req.get("http://127.0.0.1:49151", timeout=1)
        return True
    except Exception:
        return False


def _check_developer_mode(lockdown) -> Optional[bool]:
    """Return True/False/None (None = can't determine, e.g. older iOS)."""
    try:
        val = lockdown.get_value("com.apple.security.mac.amfi", "DeveloperModeStatus")
        return bool(val)
    except Exception:
        return None


def get_status() -> Dict[str, Any]:
    """
    Detailed device status for the setup checklist UI.

    step values (ordered):
      "no_device"         — no iPhone detected over USB
      "locked"            — iPhone detected but locked / unpaired
      "untrusted"         — pairing denied on the iPhone
      "no_developer_mode" — Developer Mode is off (iOS 16+)
      "no_tunnel"         — tunnel daemon not running (iOS 17+)
      "ready"             — everything set, ready to spoof

    Full response keys:
      connected, step, device_name, ios_version, model,
      developer_mode (bool|null), tunnel_running (bool), message
    """
    def _base(step: str, msg: str, **extra) -> Dict[str, Any]:
        return {
            "connected": step not in ("no_device", "locked", "untrusted"),
            "step": step,
            "device_name": None,
            "ios_version": None,
            "model": None,
            "developer_mode": None,
            "tunnel_running": False,
            "message": msg,
            **extra,
        }

    # ── 1. Attempt a USB lockdown connection ──────────────────────────────
    try:
        lockdown = _run_with_timeout(
            lambda: create_using_usbmux(autopair=False),
            timeout=CONNECT_TIMEOUT,
            label="status-connect",
        )
    except TimeoutError:
        return _base("no_device", "Couldn't reach iPhone in time — try replugging the cable.")
    except pmd_exc.NoDeviceConnectedError:
        # USB not found — check whether a Wi-Fi tunnel is active instead
        if _tunneld_is_running():
            rsd = _get_ios17_rsd()
            if rsd is not None:
                name = _get_label_from_rsd(rsd)
                return {
                    "connected": True,
                    "step": "ready",
                    "device_name": name,
                    "ios_version": rsd.product_version,
                    "model": getattr(rsd, "product_type", None),
                    "developer_mode": True,   # tunnel can't exist without dev mode
                    "tunnel_running": True,
                    "connection_type": "wifi",
                    "message": f"{name} connected via Wi-Fi — ready to spoof.",
                }
        return _base(
            "no_device",
            "No iPhone detected. Connect via USB, or make sure your iPhone is on the same Wi-Fi network with the tunnel running.",
        )
    except pmd_exc.PasswordRequiredError:
        return _base("locked", "Your iPhone is locked. Unlock it, then tap Trust when prompted.")
    except pmd_exc.UserDeniedPairingError:
        return _base("untrusted", "Pairing was denied. Unplug, replug, and tap Trust on your iPhone.")
    except Exception as exc:
        return _base("no_device", f"Can't reach iPhone over USB. Try a different cable. ({_short_error(exc)})")

    info = getattr(lockdown, "short_info", None) or {}
    ios_version = getattr(lockdown, "product_version", None)
    major = _major_ios_version(ios_version)
    name = info.get("DeviceName") or "iPhone"
    model = info.get("ProductType")

    # ── 2. Developer Mode check (iOS 16+) ────────────────────────────────
    dev_mode = _check_developer_mode(lockdown)
    if dev_mode is False:
        return _base(
            "no_developer_mode",
            f"{name} connected — Developer Mode is off. Go to Settings → Privacy & Security → Developer Mode and turn it ON.",
            device_name=name,
            ios_version=ios_version,
            model=model,
            developer_mode=False,
        )

    # ── 3. Tunnel check (iOS 17+) ─────────────────────────────────────────
    tunnel_running = False
    if major >= 17:
        tunnel_running = _tunneld_is_running()
        if not tunnel_running:
            return _base(
                "no_tunnel",
                f"{name} (iOS {ios_version}) connected — run ./backend/start-tunnel.sh in Terminal and leave it open.",
                device_name=name,
                ios_version=ios_version,
                model=model,
                developer_mode=dev_mode,
                tunnel_running=False,
            )

    # ── 4. All good ───────────────────────────────────────────────────────
    conn_type = _connection_type_from_tunneld() if tunnel_running else "usb"
    return {
        "connected": True,
        "step": "ready",
        "device_name": name,
        "ios_version": ios_version,
        "model": model,
        "developer_mode": dev_mode,
        "tunnel_running": tunnel_running,
        "connection_type": conn_type or "usb",
        "message": f"{name} (iOS {ios_version}) ready to spoof.",
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _cli(argv: list[str]) -> int:
    if len(argv) == 1 and argv[0] == "status":
        status = get_status()
        prefix = "✓" if status["connected"] else "✗"
        print(f"{prefix} {status['message']}")
        return 0 if status["connected"] else 1

    if len(argv) == 1 and argv[0] == "reset":
        result = clear_location()
        print(result)
        return 0 if result.ok else 1

    if len(argv) == 2:
        try:
            lat = float(argv[0])
            lng = float(argv[1])
        except ValueError:
            print("✗ Latitude and longitude must be numbers.")
            return 2
        result = spoof_location(lat, lng)
        print(result)
        if result.ok:
            print("Location is active. Press Enter to clear it and restore real GPS.")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass
            clear_result = clear_location()
            print(clear_result)
        return 0 if result.ok else 1

    print("Usage:")
    print("  python spoofer.py <lat> <lng>     set fake location (holds until Enter)")
    print("  python spoofer.py reset           restore real GPS")
    print("  python spoofer.py status          check iPhone connection")
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
