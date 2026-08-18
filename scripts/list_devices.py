"""Print every capture device the platform audio backend can open."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ambientqa.backends import get_backend  # noqa: E402
from ambientqa.config import default_config, load_config  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.toml"


def main() -> int:
    try:
        config = load_config(CONFIG_PATH)
    except Exception:
        # A broken config must not block the tool used to diagnose devices.
        config = default_config()
    backend = get_backend(config.audio)
    try:
        devices = backend.list_devices()
    except Exception as exc:
        print(f"Unable to enumerate audio devices: {exc}", file=sys.stderr)
        return 1
    if not devices:
        print("No input or loopback devices found.")
        return 0
    print(f"Backend: {backend.name}")
    print(f"{'TYPE':<8}  {'CHANNELS':>8}  NAME  ·  ID")
    for device in devices:
        print(
            f"{device.kind:<8}  {device.channels:>8}  "
            f"{device.display_name}  ·  {device.id}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
