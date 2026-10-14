#!/usr/bin/env python3
"""Minimal cross-platform client for the JSAUX 5.83-inch E-Ink faceplate."""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence, Tuple, Union

try:
    from bleak import BleakClient, BleakScanner
except ImportError:  # Give a useful error after argparse has displayed help.
    BleakClient = None
    BleakScanner = None

try:
    from PIL import Image, ImageDraw, ImageOps
except ImportError:
    Image = None
    ImageDraw = None
    ImageOps = None


SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
WRITE_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
NOTIFY_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

HEADER_COMMAND = 0xAC
FOOTER_COMMAND = 0xCA
HEADER_RESPONSE = 0x91
FOOTER_RESPONSE = 0x19

COMMAND_QUERY_INFO = 0x00
COMMAND_SEND_DATA = 0x02
DISPLAY_A = 0x00

WIDTH = 648
HEIGHT = 480
FRAMEBUFFER_SIZE = WIDTH * HEIGHT // 8
TARGET_NAME = "JSAUX E-INK"


@dataclass(frozen=True)
class DeviceInfo:
    is_dual_screen: bool
    color_mode: int
    width: int
    height: int
    packet_size: int
    battery: int

    @property
    def color_count(self) -> int:
        return {0: 2, 1: 3, 2: 4, 4: 6, 8: 7}.get(self.color_mode, 2)

    @property
    def is_supported(self) -> bool:
        return (
            not self.is_dual_screen
            and self.color_mode == 0
            and self.width == WIDTH
            and self.height == HEIGHT
        )


def parse_response_frame(raw: bytes, command: int) -> Optional[bytes]:
    frame = bytes(raw or b"")
    if (
        len(frame) < 4
        or frame[0] != HEADER_RESPONSE
        or frame[1] != command
        or frame[-1] != FOOTER_RESPONSE
    ):
        return None
    return frame[2:-1]


def parse_device_info(raw: bytes) -> DeviceInfo:
    payload = parse_response_frame(raw, COMMAND_QUERY_INFO)
    if payload is None or len(payload) != 9:
        raise ValueError("invalid device-info response: {}".format(bytes(raw).hex()))
    return DeviceInfo(
        is_dual_screen=payload[0] == 0x00,
        color_mode=payload[1],
        width=int.from_bytes(payload[2:4], "big"),
        height=int.from_bytes(payload[4:6], "big"),
        packet_size=int.from_bytes(payload[6:8], "big"),
        battery=max(0, min(100, payload[8])),
    )


def build_query_packet(display: int = DISPLAY_A) -> bytes:
    return bytes([HEADER_COMMAND, COMMAND_QUERY_INFO, display, FOOTER_COMMAND])


def build_data_packet(index: int, payload: bytes, payload_size: int) -> bytes:
    if not 0 <= index <= 0xFFFF:
        raise ValueError("packet index is outside the protocol range")
    if payload_size <= 0 or len(payload) > payload_size:
        raise ValueError("invalid packet payload length")
    return (
        bytes(
            [
                HEADER_COMMAND,
                COMMAND_SEND_DATA,
                (index >> 8) & 0xFF,
                index & 0xFF,
            ]
        )
        + payload.ljust(payload_size, b"\x00")
        + bytes([FOOTER_COMMAND])
    )


def normalize_name(name: Optional[str]) -> str:
    return " ".join((name or "").strip().upper().replace("_", " ").split())


def is_target_name(name: Optional[str]) -> bool:
    return normalize_name(name) == TARGET_NAME


def _require_bleak() -> None:
    if BleakClient is None or BleakScanner is None:
        raise RuntimeError("bleak is not installed; run: python3 -m pip install -e .")


def _require_pillow() -> None:
    if Image is None or ImageOps is None:
        raise RuntimeError("Pillow is not installed; run: python3 -m pip install -e .")


def prepare_image(
    source: Union[str, Path, Any],
    *,
    dither: bool = True,
    threshold: int = 128,
) -> Tuple[bytes, Any]:
    """Fit an image to the panel and return packed framebuffer bytes plus a preview."""
    _require_pillow()
    if hasattr(source, "copy") and hasattr(source, "mode"):
        image = source.copy()
    else:
        with Image.open(source) as opened:
            image = opened.copy()

    image = ImageOps.exif_transpose(image)
    if image.mode == "RGBA":
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        image = background
    else:
        image = image.convert("RGB")

    image.thumbnail((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (WIDTH, HEIGHT), "white")
    canvas.paste(image, ((WIDTH - image.width) // 2, (HEIGHT - image.height) // 2))

    if dither:
        monochrome = canvas.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    else:
        cutoff = max(0, min(255, int(threshold)))
        monochrome = canvas.convert("L").point(
            lambda value: 255 if value >= cutoff else 0,
            mode="1",
        )

    framebuffer = monochrome.tobytes()
    if len(framebuffer) != FRAMEBUFFER_SIZE:
        raise RuntimeError(
            "unexpected framebuffer length: {} (expected {})".format(
                len(framebuffer), FRAMEBUFFER_SIZE
            )
        )
    return framebuffer, monochrome


def framebuffer_to_image(framebuffer: bytes) -> Any:
    _require_pillow()
    if len(framebuffer) != FRAMEBUFFER_SIZE:
        raise ValueError(
            "raw framebuffer must be exactly {} bytes".format(FRAMEBUFFER_SIZE)
        )
    return Image.frombytes("1", (WIDTH, HEIGHT), bytes(framebuffer))


def make_test_pattern(kind: str) -> Any:
    _require_pillow()
    image = Image.new("1", (WIDTH, HEIGHT), 1)
    draw = ImageDraw.Draw(image)

    if kind == "white":
        return image
    if kind == "black":
        return Image.new("1", (WIDTH, HEIGHT), 0)
    if kind == "checkerboard":
        block = 40
        for y in range(0, HEIGHT, block):
            for x in range(0, WIDTH, block):
                if (x // block + y // block) % 2 == 0:
                    draw.rectangle(
                        (x, y, min(x + block - 1, WIDTH - 1), min(y + block - 1, HEIGHT - 1)),
                        fill=0,
                    )
        return image
    if kind == "bars":
        bar_width = WIDTH // 8
        for index in range(8):
            if index % 2 == 0:
                left = index * bar_width
                right = WIDTH - 1 if index == 7 else (index + 1) * bar_width - 1
                draw.rectangle((left, 0, right, HEIGHT - 1), fill=0)
        return image
    raise ValueError("unknown pattern: {}".format(kind))


async def scan_candidates(timeout: float = 10.0) -> List[Tuple[Any, Any]]:
    """Return advertisements that match the device name or Nordic UART service."""
    _require_bleak()
    discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
    candidates: List[Tuple[Any, Any]] = []
    for device, advertisement in discovered.values():
        name = advertisement.local_name or device.name
        service_uuids = {
            value.lower() for value in (advertisement.service_uuids or [])
        }
        if is_target_name(name) or SERVICE_UUID.lower() in service_uuids:
            candidates.append((device, advertisement))
    candidates.sort(
        key=lambda item: (
            not is_target_name(item[1].local_name or item[0].name),
            -(item[1].rssi or -127),
        )
    )
    return candidates


async def resolve_device(address: Optional[str], timeout: float) -> Any:
    _require_bleak()
    if address:
        device = await BleakScanner.find_device_by_address(address, timeout=timeout)
        if device is None:
            raise RuntimeError(
                "device {} was not found during the BLE scan".format(address)
            )
        return device

    candidates = await scan_candidates(timeout)
    if not candidates:
        raise RuntimeError(
            "JSAUX E-INK was not found; wake it, confirm Bluetooth is enabled, "
            "and allow Bluetooth access for the terminal or application running Python"
        )
    return candidates[0][0]


class JSAUXEInk:
    """BLE transport exposing query_info() and send_framebuffer()."""

    def __init__(self, device: Any, *, connect_timeout: float = 15.0) -> None:
        _require_bleak()
        self.device = device
        self.client = BleakClient(device, timeout=connect_timeout)
        self._notifications: asyncio.Queue[bytes] = asyncio.Queue()
        self._notify_started = False

    async def __aenter__(self) -> "JSAUXEInk":
        await self.connect()
        return self

    async def __aexit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        await self.disconnect()

    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        self._notifications.put_nowait(bytes(data))

    def _clear_notifications(self) -> None:
        while not self._notifications.empty():
            try:
                self._notifications.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def connect(self) -> None:
        await self.client.connect()
        await self.client.start_notify(NOTIFY_UUID, self._on_notify)
        self._notify_started = True

    async def disconnect(self) -> None:
        if self._notify_started and self.client.is_connected:
            try:
                await self.client.stop_notify(NOTIFY_UUID)
            except Exception:
                pass
        self._notify_started = False
        if self.client.is_connected:
            await self.client.disconnect()

    async def _wait_for_command(self, command: int, timeout: float) -> bytes:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            frame = await asyncio.wait_for(self._notifications.get(), remaining)
            if parse_response_frame(frame, command) is not None:
                return frame

    async def query_info(self) -> DeviceInfo:
        self._clear_notifications()
        await self.client.write_gatt_char(
            WRITE_UUID,
            build_query_packet(),
            response=True,
        )
        response = await self._wait_for_command(COMMAND_QUERY_INFO, 5.0)
        info = parse_device_info(response)
        if info.packet_size <= 0 or info.packet_size + 5 > 512:
            raise RuntimeError(
                "device reported invalid packet size {}".format(info.packet_size)
            )
        return info

    async def send_framebuffer(
        self,
        framebuffer: bytes,
        *,
        info: Optional[DeviceInfo] = None,
        progress: Optional[Callable[[int, int], None]] = None,
    ) -> DeviceInfo:
        """Send one full 1-bit frame. Bit 1 is white; bit 0 is black."""
        if len(framebuffer) != FRAMEBUFFER_SIZE:
            raise ValueError(
                "framebuffer must be exactly {} bytes, got {}".format(
                    FRAMEBUFFER_SIZE, len(framebuffer)
                )
            )

        info = info or await self.query_info()
        if not info.is_supported:
            raise RuntimeError(
                "unsupported display: {}x{}, {} colors, dual={}".format(
                    info.width,
                    info.height,
                    info.color_count,
                    info.is_dual_screen,
                )
            )

        total = math.ceil(len(framebuffer) / info.packet_size)
        if total > 0x10000:
            raise RuntimeError("frame needs too many protocol packets")

        ack_notifications: Optional[bool] = None
        for index in range(total):
            start = index * info.packet_size
            chunk = framebuffer[start : start + info.packet_size]
            packet = build_data_packet(index, chunk, info.packet_size)

            self._clear_notifications()
            await self.client.write_gatt_char(WRITE_UUID, packet, response=True)
            if ack_notifications is not False:
                try:
                    response = await self._wait_for_command(
                        COMMAND_SEND_DATA,
                        0.5 if ack_notifications is None else 5.0,
                    )
                except asyncio.TimeoutError:
                    ack_notifications = False
                else:
                    payload = parse_response_frame(response, COMMAND_SEND_DATA)
                    if payload == b"\x00":
                        ack_notifications = True
                    elif payload == b"\x01":
                        raise RuntimeError(
                            "display rejected packet {}".format(index + 1)
                        )
                    else:
                        raise RuntimeError(
                            "invalid ACK for packet {}: {}".format(
                                index + 1, response.hex()
                            )
                        )

            if progress is not None:
                progress(index + 1, total)
        return info


def _print_device_info(info: DeviceInfo) -> None:
    print("Resolution: {}x{}".format(info.width, info.height))
    print("Colors: {}".format(info.color_count))
    print("Dual screen: {}".format("yes" if info.is_dual_screen else "no"))
    print("Packet payload: {} bytes".format(info.packet_size))
    print("Battery: {}%".format(info.battery))
    print("Supported: {}".format("yes" if info.is_supported else "no"))


def _progress(current: int, total: int) -> None:
    if current == total or current == 1 or current % 36 == 0:
        print(
            "\rSending: {}/{} ({:.0f}%)".format(
                current, total, current * 100 / total
            ),
            end="\n" if current == total else "",
            flush=True,
        )


async def _run_scan(args: argparse.Namespace) -> int:
    candidates = await scan_candidates(args.timeout)
    if not candidates:
        print("No likely JSAUX E-INK devices found.")
        return 1
    for device, advertisement in candidates:
        print(
            "{}  name={!r}  RSSI={} dBm".format(
                device.address,
                advertisement.local_name or device.name or "",
                advertisement.rssi,
            )
        )
    return 0


async def _run_info(args: argparse.Namespace) -> int:
    device = await resolve_device(args.address, args.timeout)
    print("Connecting to {}...".format(device.address))
    async with JSAUXEInk(device) as display:
        _print_device_info(await display.query_info())
    return 0


async def _send(args: argparse.Namespace, framebuffer: bytes) -> int:
    device = await resolve_device(args.address, args.timeout)
    print("Connecting to {}...".format(device.address))
    async with JSAUXEInk(device) as display:
        info = await display.query_info()
        _print_device_info(info)
        print("Sending {} framebuffer bytes...".format(len(framebuffer)))
        await display.send_framebuffer(framebuffer, info=info, progress=_progress)
    print("Transfer complete. The protocol does not provide a panel-refresh ACK.")
    return 0


async def _run_send_image(args: argparse.Namespace) -> int:
    framebuffer, preview = prepare_image(
        args.image,
        dither=args.dither == "floyd",
        threshold=args.threshold,
    )
    if args.preview:
        preview.save(args.preview)
        print("Saved preview to {}".format(args.preview))
    return await _send(args, framebuffer)


async def _run_send_raw(args: argparse.Namespace) -> int:
    framebuffer = Path(args.framebuffer).read_bytes()
    if args.preview:
        framebuffer_to_image(framebuffer).save(args.preview)
        print("Saved preview to {}".format(args.preview))
    return await _send(args, framebuffer)


async def _run_pattern(args: argparse.Namespace) -> int:
    pattern = make_test_pattern(args.pattern)
    framebuffer = pattern.tobytes()
    if args.preview:
        pattern.save(args.preview)
        print("Saved preview to {}".format(args.preview))
    return await _send(args, framebuffer)


def _add_target_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--address",
        help="BLE identifier (a UUID on macOS); auto-discovered when omitted",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="BLE discovery timeout in seconds (default: 10)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Control the JSAUX 5.83-inch E-Ink faceplate over BLE."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="scan for likely displays")
    scan_parser.add_argument("--timeout", type=float, default=10.0)
    scan_parser.set_defaults(handler=_run_scan)

    info_parser = subparsers.add_parser("info", help="query display geometry and battery")
    _add_target_options(info_parser)
    info_parser.set_defaults(handler=_run_info)

    image_parser = subparsers.add_parser("send", help="prepare and send an image")
    image_parser.add_argument("image", type=Path)
    image_parser.add_argument(
        "--dither",
        choices=("floyd", "threshold"),
        default="floyd",
        help="1-bit conversion method (default: floyd)",
    )
    image_parser.add_argument("--threshold", type=int, default=128)
    image_parser.add_argument("--preview", type=Path)
    _add_target_options(image_parser)
    image_parser.set_defaults(handler=_run_send_image)

    raw_parser = subparsers.add_parser(
        "raw", help="send a packed 38,880-byte framebuffer"
    )
    raw_parser.add_argument("framebuffer", type=Path)
    raw_parser.add_argument("--preview", type=Path)
    _add_target_options(raw_parser)
    raw_parser.set_defaults(handler=_run_send_raw)

    pattern_parser = subparsers.add_parser("pattern", help="send a test pattern")
    pattern_parser.add_argument(
        "pattern",
        choices=("checkerboard", "bars", "black", "white"),
    )
    pattern_parser.add_argument("--preview", type=Path)
    _add_target_options(pattern_parser)
    pattern_parser.set_defaults(handler=_run_pattern)

    return parser


async def _async_main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return await args.handler(args)


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        return asyncio.run(_async_main(argv))
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as error:
        print("Error: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
