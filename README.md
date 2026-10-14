# Open JSAUX E-Ink

An unofficial, cross-platform client for sending custom 1-bit frames to the
JSAUX 5.83-inch E-Ink faceplate. The protocol was documented from the official
V1.1 SteamOS installer and verified against real hardware from macOS.

The E-Ink faceplate is a BLE peripheral, not a USB framebuffer. USB-C charges
it; display data is sent through Bluetooth Low Energy.

## Compatibility

The client uses [Bleak](https://github.com/hbldh/bleak), which provides native
BLE backends for:

- Linux through BlueZ
- macOS through CoreBluetooth
- Windows through WinRT

The protocol and image tests are platform-independent. Real hardware has
currently been tested on macOS; Linux and Windows hardware reports are welcome.

## Set up

```sh
git clone https://github.com/naruse/open-jsaux-eink.git
cd open-jsaux-eink
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools
.venv/bin/python -m pip install -e .
```

On macOS, the first scan may request Bluetooth permission for the terminal or
IDE running Python. If discovery returns nothing, check **System Settings →
Privacy & Security → Bluetooth**, wake the faceplate, and keep it nearby. On
Linux, ensure BlueZ and the Bluetooth service are installed and running.

The faceplate does not need conventional Bluetooth pairing and may not appear
in macOS's Bluetooth device list. The client discovers and connects to its BLE
GATT service directly. Attaching USB-C power makes the tested unit advertise;
it does not enumerate as a USB data device.

## Use

Find the panel:

```sh
.venv/bin/jsaux-eink scan
.venv/bin/jsaux-eink info
```

`info` reports the display geometry, packet size, battery, standard PnP
identity, firmware-revision availability, and whether an OTA endpoint is
advertised. The command never writes to that endpoint.

Prepare and send any static image:

```sh
.venv/bin/jsaux-eink send photo.png --preview prepared.png
```

Use a hard threshold instead of Floyd-Steinberg dithering:

```sh
.venv/bin/jsaux-eink send art.png --dither threshold --threshold 150
```

Send a harmless test pattern:

```sh
.venv/bin/jsaux-eink pattern checkerboard --preview checkerboard.png
```

Send an already-packed framebuffer:

```sh
.venv/bin/jsaux-eink raw frame.bin --preview frame.png
```

A raw frame must be exactly 38,880 bytes: 648 × 480 pixels, row-major,
most-significant bit first, where 1 is white and 0 is black.

macOS exposes a CoreBluetooth UUID instead of a Bluetooth MAC address. Pass a
specific identifier reported by `scan` when more than one device is nearby:

```sh
.venv/bin/jsaux-eink info --address <UUID>
```

## Python API

`JSAUXEInk.send_framebuffer()` is the low-level framebuffer interface.
`prepare_image()` produces correctly packed bytes:

```python
import asyncio

from jsaux_eink import JSAUXEInk, prepare_image, resolve_device


async def update():
    device = await resolve_device(address=None, timeout=10)
    frame, _preview = prepare_image("photo.png")
    async with JSAUXEInk(device) as display:
        await display.send_framebuffer(frame)


asyncio.run(update())
```

## How dynamic data works

The panel does not receive semantic weather, game, or performance data. The
host application gathers that data, renders a complete monochrome image, and
sends the resulting 38,880-byte framebuffer:

```text
data source -> host-side renderer -> 648x480 1-bit frame -> BLE -> panel
```

The official SteamOS app obtains weather from Open-Meteo after IP-based
location lookup. It detects the running Steam game from processes and
`gameprocess_log.txt`, reads names and playtime from Steam VDF files, and
loads artwork from Steam's local cache or CDN. All layouts are rendered
host-side with Pillow.

See [PROTOCOL.md](PROTOCOL.md) for the packet format, firmware observations,
and official installer architecture.

## Limits

- This currently supports the monochrome 648 × 480 faceplate only.
- It does not implement firmware updates (`0x04`), grayscale modes, or partial
  refreshes because the installer does not expose those formats.
- The protocol acknowledges transport, not completion of the physical E-Ink
  refresh.
- Repeated full refreshes use battery and may increase ghosting; avoid treating
  this panel like a high-frame-rate display.

## License

MIT. This project is unofficial and is not affiliated with or endorsed by
JSAUX.
