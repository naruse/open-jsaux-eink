# JSAUX E-Ink BLE protocol

This documents the protocol recovered from the official JSAUX E-Ink V1.1
SteamOS installer and verified against real hardware. The vendor installer is
not redistributed by this project. It is plain Python; no disassembly was
needed.

## Hardware

- Model: 5.83-inch monochrome JSAUX E-Ink faceplate
- Advertised name: `JSAUX E-INK`
- Actual framebuffer: **648 × 480**, not the 640 × 480 stated on the store page
- Transport: Bluetooth Low Energy 5.0
- USB-C: charging only

The software rejects hardware unless its info response says 648 × 480,
two-color, and single-screen.

## GATT

The device uses Nordic UART Service:

- Service: `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`
- Host write: `6E400002-B5A3-F393-E0A9-E50E24DCCA9E`
- Device notify: `6E400003-B5A3-F393-E0A9-E50E24DCCA9E`

All observed host writes request a GATT response.

## Framing

Host command:

```text
AC <command> <payload...> CA
```

Device response:

```text
91 <command> <payload...> 19
```

There is no checksum or CRC in the V1.1 host code.

### Query device info (`00`)

Request:

```text
AC 00 <display> CA
```

The production sender uses display `00` (side A).

The response payload is nine bytes:

```text
<dual> <color> <width:u16be> <height:u16be> <packet-size:u16be> <battery>
```

`dual == 00` means dual-screen. Color mode `00` means monochrome. The target
device normally reports packet size 108 bytes.

### Send display data (`02`)

One packet is:

```text
AC 02 <index:u16be> <packet-size bytes> CA
```

The last chunk is zero-padded. Packet indexes start at zero. After a write,
some firmware versions notify:

```text
91 02 00 19  # accepted
91 02 01 19  # rejected
```

The official sender probes for this notification for 0.5 seconds. If none
arrives, it relies on GATT write responses for the rest of the frame. There is
no separate commit command and no notification confirming that the physical
panel refresh completed.

Opcode `04` is labelled OTA in the installer, but its packet format is not
implemented there.

## Firmware observations

The tested panel exposes the standard Device Information service with this PnP
ID:

```text
02 8A 24 66 82 01 00
```

Interpreted as Bluetooth PnP ID fields, this is vendor source `02`, vendor
`248A`, product `8266`, and product version `0001`. Together with the GATT
layout, this strongly suggests a Telink TLSR8266-family BLE platform, but does
not prove the exact chip fitted to the PCB.

The panel also exposes:

```text
Service:        00010203-0405-0607-0809-0A0B0C0D1919
Characteristic: 00010203-0405-0607-0809-0A0B0C0D2B12
Properties:     read, write, write-without-response, notify
User label:     OTA
```

The characteristic UUID is associated with Telink BLE OTA implementations.
Its value reads as `00`. The standard Firmware Revision characteristic
(`2A26`) is absent, so the exact installed firmware version cannot currently
be read. The official installer contains no firmware image and its UI says
firmware updates are unsupported.

Do not write to this characteristic without a known-good firmware image and a
recovery method. Telink BLE OTA is normally a firmware upload path, not a way
to back up existing flash.

## Framebuffer

- Length: `648 * 480 / 8 = 38,880` bytes
- Pixel order: rows from top to bottom, columns from left to right
- Byte order: leftmost pixel in the most significant bit
- Polarity: `1` is white, `0` is black
- Compression: none

The official image path fits the source inside 648 × 480, centers it on white,
converts it with Pillow's default 1-bit Floyd-Steinberg dithering, and sends
the resulting packed bytes.

## Official installer architecture

The relevant path is:

```text
Desktop/Decky UI
  -> localhost daemon RPC (:39062)
  -> main.py MonoInkCore
  -> BleakClient
  -> Nordic UART GATT
  -> faceplate
```

In the separately distributed vendor installer, protocol constants and a
reference client are in
`payload/jsaux-eink-display/eink_display.py`. The production transfer loop is
`MonoInkCore._send_prepared_bytes` in `main.py`. SteamOS-specific BlueZ,
`bluetoothctl`, systemd, and Decky code is not required on macOS; Bleak uses
CoreBluetooth there.
