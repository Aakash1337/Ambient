"""Print PyAudioWPatch input and WASAPI loopback devices."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ambientqa.audio import list_capture_devices


def main() -> int:
    try:
        devices = list_capture_devices()
    except Exception as exc:
        print(f"Unable to enumerate audio devices: {exc}", file=sys.stderr)
        return 1
    if not devices:
        print("No input or loopback devices found.")
        return 0
    print(f"{'INDEX':>5}  {'TYPE':<8}  {'CHANNELS':>8}  NAME")
    for device in devices:
        print(
            f"{int(device['index']):>5}  {device['kind']:<8}  "
            f"{int(device.get('maxInputChannels', 0)):>8}  {device.get('name', '')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

