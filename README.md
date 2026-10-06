# LY738 Watch Lab

Small Python utilities for experimenting with a LY738-series BLE watch:

- `command.py` discovers the watch's vendor GATT command characteristic and
  offers a few logged command tests. It can optionally replay a complete watch
  face transfer from a local Android `logcat.txt` capture.
- `fetch_assets.py` extracts watch-face material URLs from a local
  `logcat.txt` capture and downloads the selected CDN assets after validating
  their host and logged sizes.

These are experimental, device-specific tools, not a supported watch SDK.
Sensor commands may behave differently across firmware. Blood-pressure
measurements are not medical readings. A face upload replaces the current face;
an interrupted transfer may leave the watch in an incomplete state.

## Setup

Fedora requires BlueZ and a working Bluetooth adapter. Python 3.10 or newer is
recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Disconnect the watch from its companion phone app before connecting from this
computer. To use the BLE command tool:

```bash
export WATCH_MAC="AA:BB:CC:DD:EE:FF"
python command.py
```

The address is requested interactively if `WATCH_MAC` is unset. The tool asks
for confirmation before sending any command and uses the watch's discovered
`FFFF/FF22` vendor characteristic rather than a hard-coded GATT handle.

`fetch_assets.py` requires a local `logcat.txt` capture containing the
companion app's watch-face metadata:

```bash
python fetch_assets.py
```

The capture, downloaded assets, virtual environment, and device-specific
experimental scripts are intentionally excluded from version control. Do not
publish logs or captures without checking them for device identifiers and
personal data.
