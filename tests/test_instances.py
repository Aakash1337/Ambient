from __future__ import annotations

import os
from pathlib import Path

from ambientqa.instances import HEARTBEAT_TTL_S, InstanceRegistry


def test_single_instance_counts_itself(tmp_path: Path) -> None:
    registry = InstanceRegistry(tmp_path / "reg", clock=lambda: 1000.0)
    assert registry.heartbeat_and_count() == 1
    assert (tmp_path / "reg" / str(os.getpid())).exists()


def test_fresh_peer_heartbeats_are_counted(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    registry = InstanceRegistry(root, clock=lambda: 1000.0)
    registry.heartbeat_and_count()
    peer = root / "99999"
    peer.touch()
    os.utime(peer, (999.0, 999.0))
    assert registry.heartbeat_and_count() == 2


def test_stale_heartbeats_are_pruned_not_counted(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    registry = InstanceRegistry(root, clock=lambda: 1000.0)
    dead = root / "88888"
    root.mkdir()
    dead.touch()
    os.utime(dead, (1000.0 - HEARTBEAT_TTL_S - 1, 1000.0 - HEARTBEAT_TTL_S - 1))
    assert registry.heartbeat_and_count() == 1
    assert not dead.exists(), "a crashed instance's file must be cleaned up"


def test_close_removes_own_heartbeat(tmp_path: Path) -> None:
    registry = InstanceRegistry(tmp_path / "reg", clock=lambda: 1000.0)
    registry.heartbeat_and_count()
    registry.close()
    assert not (tmp_path / "reg" / str(os.getpid())).exists()


def test_unusable_registry_still_reports_this_instance(tmp_path: Path) -> None:
    # A file where the directory should be makes every filesystem call fail;
    # the count must degrade to "at least me", never crash the status bar.
    blocker = tmp_path / "reg"
    blocker.write_text("not a directory")
    registry = InstanceRegistry(blocker, clock=lambda: 1000.0)
    assert registry.heartbeat_and_count() == 1
