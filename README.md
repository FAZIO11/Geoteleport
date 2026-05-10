# Location Spoofer

**Free, open-source Mac app to fake your iPhone's GPS location — no jailbreak, no Apple account needed.**

Pick any spot on a map, click **Move iPhone Here**, and your iPhone thinks it's there.

<br>

<p align="center">
  <a href="https://github.com/FAZIO11/Geoteleport/releases/latest/download/LocationSpoofer.dmg">
    <img src="https://img.shields.io/badge/⬇_Download_for_Mac-LocationSpoofer.dmg-blue?style=for-the-badge&logo=apple&logoColor=white" alt="Download LocationSpoofer">
  </a>
</p>

<br>

---

## What you need

- A **Mac** running macOS 12 (Monterey) or newer
- An **iPhone** with a data-transfer USB cable (not a charge-only cable)
- **iOS 17 or newer?** One extra step is needed — the app will walk you through it

---

## Getting started

### 1. Download and open the app

Click the download button above to get `LocationSpoofer.dmg`.

Open the DMG, then drag **LocationSpoofer** into your Applications folder.

> **macOS security warning (one-time)**
>
> Because this app isn't notarized by Apple, macOS will block it the first time:
>
> *"LocationSpoofer.app cannot be opened because Apple cannot check it for malicious software."*
>
> **Fix:** Right-click (or Control-click) the app → **Open** → click **Open** in the dialog.
>
> macOS only asks once. After that, just double-click as normal.

### 2. Connect your iPhone

Plug your iPhone into your Mac with a USB cable.

If your iPhone shows a **"Trust This Computer?"** popup — tap **Trust** and enter your passcode.

### 3. Open Location Spoofer

Double-click **LocationSpoofer** in your Applications folder.

A window opens with a map. The app will guide you through any remaining setup (like enabling Developer Mode on iOS 17+).

On iOS 17+, macOS will ask for your Mac password the first time you open the app — that's how Location Spoofer gets permission to talk to your iPhone. Type your password and the setup card turns green.

### 4. Pick a location and go

- **Click anywhere on the map** to drop a pin, then hit **Move iPhone Here**
- **Or type a place name** in the search bar and select from the suggestions

Your iPhone's GPS will switch to that location within a few seconds — in every app.

### 5. Stop spoofing

Hit **Stop Spoofing** in the app to get your real GPS back.

Or just unplug the iPhone — the fake location disappears on its own.

---

## iOS 17+ setup (one extra step)

Apple tightened how GPS simulation works in iOS 17. You need to enable **Developer Mode** on your iPhone once:

1. On your iPhone: **Settings → Privacy & Security → Developer Mode → toggle ON**
2. The phone reboots. After reboot, tap **Turn On** and enter your passcode.

> If you don't see Developer Mode in Settings, plug the iPhone into your Mac, open Location Spoofer once, then reboot the phone — it will appear.

That's it. Location Spoofer handles the rest — when it needs admin permission to talk to your iPhone (the developer tunnel), it asks with the standard macOS password dialog. No Terminal commands required.

---

## Troubleshooting

| What you see | What to try |
|---|---|
| "No iPhone detected." | Replug the cable. Try a different cable — charge-only cables won't work. |
| "iPhone is locked." | Wake the phone, unlock it, then tap **Trust** on the popup. |
| "Pairing was denied." | Unplug, replug, and tap **Trust** when the popup appears. |
| Yellow "needs tunnel" / "Allow access" prompt | iOS 17+ device — click **Allow access** and type your Mac password when macOS asks. |
| Maps still shows real location | Force-quit Maps and reopen it — some apps cache location for a few seconds. |
| Nothing happens after clicking | Make sure the status indicator is green before spoofing. |

---

## Privacy

No data leaves your computer. No account, no analytics, no tracking.

The only network calls the app makes are to load map tiles (OpenStreetMap) and look up place names when you search (Nominatim). Both are free public services.

---

<details>
<summary><strong>Building from source</strong></summary>

<br>

**Requirements:** macOS 12+, Python 3.10–3.14

```bash
git clone https://github.com/FAZIO11/Geoteleport.git
cd LocationSpoofer
./build/build.sh
```

This installs all dependencies, bundles everything with PyInstaller, and produces:
- `dist/LocationSpoofer.app`
- `dist/LocationSpoofer.dmg`

To use a specific Python version:

```bash
PYTHON=/opt/homebrew/bin/python3.12 ./build/build.sh
```

</details>

<details>
<summary><strong>Running in dev mode</strong></summary>

<br>

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
# → opens the native window
# → set LOCATION_SPOOFER_HEADLESS=1 to skip the window (server only on :8765)
# → set LOCATION_SPOOFER_BROWSER=1 to open in your browser instead
```

Test the spoofer from the command line directly:

```bash
python spoofer.py status           # check if iPhone is connected
python spoofer.py 48.8584 2.2945   # set location (Eiffel Tower)
python spoofer.py reset            # restore real GPS
```

</details>

<details>
<summary><strong>How it works</strong></summary>

<br>

- `pymobiledevice3` opens a USB (or Wi-Fi) connection to your iPhone via macOS's built-in `usbmuxd` daemon.
- It uses the same private developer service Xcode uses to simulate location during app development (`LocationSimulation` via DVT).
- The fake GPS persists until you hit Stop Spoofing or unplug — identical behaviour to Xcode's location simulator.
- On iOS 17+, Apple requires an authenticated network tunnel (`pymobiledevice3 remote tunneld`) before the service can be reached. The app launches this helper automatically with `osascript -e 'do shell script ... with administrator privileges'` — that's why macOS asks for your password the first time.
- Wi-Fi spoofing works automatically when the tunnel is running and your iPhone is on the same network — no cable needed.

</details>

<details>
<summary><strong>Limitations</strong></summary>

<br>

- **macOS only** — no Windows or Linux support
- **No route playback** — location is a fixed point, not a moving path (coming in v2)
- **iOS 17+ requires Developer Mode** — see setup above
- **Not on the App Store** — Apple doesn't allow apps like this

</details>

---

## License

MIT — use it, fork it, ship it. [Full text](LICENSE)
