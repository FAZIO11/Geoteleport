#!/usr/bin/env bash
# start-tunnel.sh — start the pymobiledevice3 tunnel daemon required for iOS 17+.
#
# Usage:
#   ./start-tunnel.sh
#
# Will prompt for your Mac password (sudo is required because the daemon
# creates a virtual network interface to talk to the iPhone).
#
# Leave this terminal window open while you use Location Spoofer.
# Press Ctrl-C in this window when you're done to stop the tunnel.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

VENV_PY="$(pwd)/.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "✗ Couldn't find $VENV_PY"
  echo "  Create the venv first:"
  echo "      cd backend"
  echo "      python3 -m venv .venv"
  echo "      source .venv/bin/activate"
  echo "      pip install -r requirements.txt"
  exit 1
fi

echo "==> Starting pymobiledevice3 tunnel daemon (Ctrl-C to stop)..."
echo "    Mac password may be required."
echo

# sudo with -E preserves the env we just set; -E isn't strictly needed here
# but is harmless. We pass the venv's python explicitly so sudo doesn't fall
# back to /usr/bin/python3 (which doesn't have pymobiledevice3 installed).
exec sudo "$VENV_PY" -m pymobiledevice3 remote tunneld
