# Location Spoofer

A free, open-source Mac app that lets anyone change their iPhone's GPS
location with a click. No jailbreak, no Apple Developer account, no
sketchy profiles — just plug your iPhone in over USB, pick a spot on a
map, and tap **Move iPhone Here**. Built for non-technical users:
launch the app, get a map, get on with your life.

---

## What you need

- A Mac running **macOS 12 (Monterey)** or newer
- An **iPhone** (iOS 12 through iOS 16 work out of the box; iOS 17+ needs
  one extra setup step — see [iOS 17+ note](#ios-17-note) below)
- A working **USB / USB-C cable** that can transfer data (not a
  charge-only cable)
- The first time you connect the iPhone, you'll be asked to **Trust** the
  computer — tap "Trust" and enter your passcode

That's it. No account. No subscription. No internet required after the
first launch (only the map tiles and the place search use the internet).

---

## Quick start (use the prebuilt app)

1. Download `LocationSpoofer.app` (or build it yourself — see below).
2. Plug your iPhone into your Mac. Tap **Trust** on the phone if asked.
3. Double-click `LocationSpoofer.app`.
4. Your browser opens with a map. Click anywhere — or search a place —
   and hit **Move iPhone Here**.
5. To stop spoofing and get your real GPS back, hit **Stop spoofing**.

> **First-launch warning:** Because this app isn't signed by a paid Apple
> Developer account, macOS will block it the first time. See
> [Bypassing the "unverified developer" warning](#unverified-developer)
> below.

---

## Run it in dev mode (no .app needed)

If you have Python and want to hack on it:

```bash
# 1. Install dependencies
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Run the server
python main.py
# → opens http://localhost:8765 in your browser
```

You can also test the spoofer directly from the command line, no UI:

```bash
# from inside backend/ with the venv active
python spoofer.py status                 # check if iPhone is connected
python spoofer.py 48.8584 2.2945         # set location to the Eiffel Tower
python spoofer.py reset                  # restore real GPS
```

---

## Build the .app yourself

```bash
./build/build.sh
```

The script:

1. Installs `pymobiledevice3`, FastAPI, uvicorn and PyInstaller.
2. Bundles `backend/main.py`, `backend/spoofer.py` and
   `frontend/index.html` into a single `.app`.
3. Outputs `dist/LocationSpoofer.app`.

To use a specific Python interpreter:

```bash
PYTHON=/opt/homebrew/bin/python3.12 ./build/build.sh
```

---

<a id="unverified-developer"></a>
## Bypassing the "unverified developer" warning

The first time you (or anyone else) opens `LocationSpoofer.app`, macOS
will refuse with a message like:

> "LocationSpoofer.app" cannot be opened because the developer cannot
> be verified.

This is **not** a bug — it just means the app isn't signed by a $99/year
Apple Developer account. To get past it (one-time, per Mac):

**Option A — Right-click open**

1. In Finder, **right-click** (or Control-click) `LocationSpoofer.app`.
2. Choose **Open**.
3. In the dialog that appears, click **Open** again.

**Option B — System Settings**

1. Try to open the app normally and dismiss the warning.
2. Open **System Settings → Privacy & Security**.
3. Scroll down — you'll see a message about LocationSpoofer being
   blocked. Click **Open Anyway**.

After that, double-clicking always works. macOS only asks once per app
per Mac.

---

<a id="ios-17-note"></a>
## iOS 17+ setup (one extra step)

Apple changed how developer tools talk to the iPhone in iOS 17. Setting
the location now requires an authenticated network "tunnel" to the
phone, which has to be started by a process running as root. There is
no workaround that doesn't require sudo — Apple made it this way.

You need **two** things on iOS 17+:

### 1. Enable Developer Mode on the iPhone (one time, ever)

1. **Settings → Privacy & Security → Developer Mode → toggle ON**
2. The phone reboots.
3. After reboot, unlock the phone. A popup asks "Turn On Developer
   Mode?" — tap **Turn On** and enter your passcode.

If you don't see the **Developer Mode** row, plug the iPhone into the
Mac, run Location Spoofer once, then reboot the phone — the row will
appear after that.

### 2. Start the tunnel daemon (each Mac reboot)

In a Terminal window, from the project root:

```bash
./backend/start-tunnel.sh
```

It'll ask for your Mac password (sudo is required to create a virtual
network interface for the iPhone). Then it'll sit there showing log
output. **Leave that window open.**

> Why not just `sudo python3 -m pymobiledevice3 remote tunneld`?
> Because `sudo` uses your system Python, not the venv Python where
> `pymobiledevice3` is actually installed. The helper script points
> sudo at the right interpreter for you.

Now launch `LocationSpoofer.app` (or `python main.py` in dev mode).
The iPhone status pill at the top of the UI should flip from yellow
("needs tunnel") to green ("ready") within a few seconds.

When you're done, press **Ctrl-C** in the tunnel terminal to shut it down.

#### Troubleshooting the tunnel

- **Status stays yellow.** Wait ~10 seconds — the daemon takes a moment
  to find the device. Still yellow? Unplug and replug the iPhone.
- **`start-tunnel.sh` says "Couldn't find .venv/bin/python".** You
  haven't created the venv yet. Go to *Run it in dev mode* above.
- **`sudo` won't accept your password.** Make sure your user is an
  Administrator on the Mac (System Settings → Users & Groups).
- **The daemon prints `Developer Mode disabled`.** Go back to step 1
  and re-enable Developer Mode on the iPhone.

---

## Troubleshooting

| What you see | What to do |
|---|---|
| "No iPhone detected." | Replug the cable. Try a different cable (charge-only cables won't work). Try a different USB port. |
| "iPhone is locked." | Wake the phone, unlock with passcode/Face ID, tap **Trust** on the popup. |
| "Pairing was denied." | Unplug, replug, and tap **Trust** this time when the popup appears. |
| Yellow "needs tunnel" status | You're on iOS 17+. Run the `tunneld` command above. |
| Spoof works but Maps still shows real location | Force-quit Maps and reopen it. Some apps cache the last known location for a few seconds. |
| Want to undo the spoof | Hit **Stop spoofing** in the app. Or just unplug the iPhone and reboot it. |
| `pip install` fails building `pydantic-core` from source | You're on a Python version older than 3.10 or newer than what the pinned `pydantic` ships wheels for. Easiest fix: `brew install python@3.12` and re-run inside that Python. |

---

## How it works (short version)

- `pymobiledevice3` opens a USB connection to your iPhone via macOS's
  built-in `usbmuxd` daemon.
- It uses the same private-but-stable developer service that Xcode uses
  to simulate location during app development (`LocationSimulation`).
- The fake location persists until you call `clear` or unplug the
  phone — exactly the same behaviour as Xcode.

No data leaves your computer. No analytics. No phone-home. The only
network calls the app makes are:

- OpenStreetMap tiles (the map background)
- Nominatim (place name → coordinates, used by the search bar)

Both are free public services and require no account.

---

## Limitations (v1)

- **macOS only.** No Windows or Linux support.
- **USB only.** No Wi-Fi spoofing.
- **No GPX route playback.** Coordinates are static. (Coming in v2.)
- **iOS 17+ needs the tunneld helper** (see above).
- **Not on the App Store.** Apple won't allow apps like this.

---

## License

MIT License. Use it, fork it, ship it. No warranty.

```
MIT License

Copyright (c) 2026 Location Spoofer contributors

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be included
in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```
