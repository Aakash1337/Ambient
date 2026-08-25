"""Interactively identify and select an Ambient capture device."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ambientqa.audio_devices import (  # noqa: E402
    AudioDeviceSession,
    CaptureDevice,
    MeterReading,
)
from ambientqa.backends import get_backend  # noqa: E402
from ambientqa.config import load_config  # noqa: E402
from ambientqa.config_write import set_audio_device  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config.toml"


def _meter_text(reading: MeterReading) -> str:
    if reading.unavailable is not None:
        return f"unavailable: {reading.unavailable}"
    return (
        f"{'█' * reading.bar}{'░' * (18 - reading.bar)}  "
        f"peak {reading.peak_db:5.1f} dB  RMS {reading.rms_db:5.1f} dB"
    )


def _print_devices(
    devices: list[CaptureDevice],
    readings: dict[tuple[str, str], MeterReading],
    *,
    numbered: bool,
) -> None:
    number = 0
    for kind, heading in (("mic", "MICROPHONES"), ("loopback", "SYSTEM AUDIO")):
        print(heading)
        matching = [device for device in devices if device.kind == kind]
        if not matching:
            print("  (none)")
        for device in matching:
            number += 1
            prefix = f"{number:>2}. " if numbered else "  "
            print(f"{prefix}{device.display_name}")
            print(f"    {_meter_text(readings.get(device.key, MeterReading()))}")
        print()


def _meter_for(
    session: AudioDeviceSession,
    seconds: float,
    *,
    live: bool,
) -> dict[tuple[str, str], MeterReading]:
    deadline = time.monotonic() + seconds
    maxima: dict[tuple[str, str], MeterReading] = {}
    first = True
    while first or time.monotonic() < deadline:
        first = False
        readings = session.snapshot()
        for key, reading in readings.items():
            previous = maxima.get(key)
            if reading.unavailable is not None:
                maxima[key] = reading
            elif previous is None or reading.peak > previous.peak:
                maxima[key] = reading
        if live:
            print("\x1b[2J\x1b[H", end="")
            print(f"Speak now — comparing every device for {seconds:g} seconds\n")
            _print_devices(session.devices, readings, numbered=True)
        time.sleep(0.1)
    return maxima


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare live capture levels and choose an Ambient device."
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=4.0,
        help="metering window in seconds (default: 4)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print devices and measured levels without prompting",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.seconds < 0:
        print("--seconds must be non-negative", file=sys.stderr)
        return 2
    try:
        config = load_config(CONFIG_PATH)
        session = AudioDeviceSession.open(
            get_backend(config.audio),
            active_mic=config.audio.mic_device,
            active_loopback=config.audio.output_device,
        )
    except Exception as exc:
        print(f"Unable to enumerate audio devices: {exc}", file=sys.stderr)
        return 1

    try:
        if not session.devices:
            print("No microphone or system-audio endpoints found.")
            return 0
        maxima = _meter_for(session, args.seconds, live=not args.list)
        if args.list:
            _print_devices(session.devices, maxima, numbered=False)
            return 0
        _print_devices(session.devices, maxima, numbered=True)
        try:
            raw = input(f"Choose a device [1-{len(session.devices)}], or Enter to cancel: ")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return 0
        if not raw.strip():
            print("Cancelled.")
            return 0
        try:
            choice = int(raw)
        except ValueError:
            print("Choice must be a number.", file=sys.stderr)
            return 2
        if not 1 <= choice <= len(session.devices):
            print("Choice is outside the listed range.", file=sys.stderr)
            return 2
        selected = session.devices[choice - 1]
        key = "mic_device" if selected.kind == "mic" else "output_device"
        set_audio_device(CONFIG_PATH, key, selected.name)
        target = "microphone" if selected.kind == "mic" else "system audio"
        print(f"Selected {target}: {selected.name}")
        print(f"Updated {CONFIG_PATH}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
