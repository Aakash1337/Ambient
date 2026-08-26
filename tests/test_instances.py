from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from ambientqa import __main__ as main_module
from ambientqa.config import default_config
from ambientqa import instances as instances_module
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


def test_stale_heartbeat_for_a_live_pid_is_not_pruned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "reg"
    registry = InstanceRegistry(root, clock=lambda: 1000.0)
    peer = root / "77777"
    root.mkdir()
    peer.touch()
    os.utime(peer, (900.0, 900.0))
    monkeypatch.setattr(
        instances_module,
        "_ambientqa_pid_alive",
        lambda value: value == "77777",
    )

    assert registry.heartbeat_and_count() == 2
    assert peer.exists()


def test_macos_stale_pid_identity_uses_ps_without_proc(monkeypatch) -> None:
    monkeypatch.setattr(instances_module.sys, "platform", "darwin")
    monkeypatch.setattr(instances_module.os, "name", "posix")
    monkeypatch.setattr(instances_module.os, "kill", lambda _pid, _signal: None)
    calls: list[list[str]] = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, stdout="/usr/bin/python3 -m ambientqa --web\n", stderr=""
        )

    monkeypatch.setattr(instances_module.subprocess, "run", run)
    assert instances_module._ambientqa_pid_alive("1234")
    assert calls == [["ps", "-p", "1234", "-o", "command="]]


def test_close_removes_own_heartbeat(tmp_path: Path) -> None:
    registry = InstanceRegistry(tmp_path / "reg", clock=lambda: 1000.0)
    registry.heartbeat_and_count()
    registry.close()
    assert not (tmp_path / "reg" / str(os.getpid())).exists()


def test_exclusive_lock_is_held_until_registry_closes(tmp_path: Path) -> None:
    first = InstanceRegistry(tmp_path / "reg")
    second = InstanceRegistry(tmp_path / "reg")

    assert first.claim_exclusive()
    assert not second.claim_exclusive()
    first.close()
    assert second.claim_exclusive()
    second.close()


def test_unusable_registry_still_reports_this_instance(tmp_path: Path) -> None:
    # A file where the directory should be makes every filesystem call fail;
    # the count must degrade to "at least me", never crash the status bar.
    blocker = tmp_path / "reg"
    blocker.write_text("not a directory")
    registry = InstanceRegistry(blocker, clock=lambda: 1000.0)
    assert registry.heartbeat_and_count() == 1


def test_startup_refuses_a_second_pipeline_before_controller_load(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    closed = False

    class PeerRegistry:
        def claim_exclusive(self) -> bool:
            return True

        def heartbeat_and_count(self) -> int:
            return 2

        def close(self) -> None:
            nonlocal closed
            closed = True

    def controller_must_not_load(*_args, **_kwargs):
        raise AssertionError("controller/model construction ran before the guard")

    monkeypatch.setattr(main_module, "load_config", lambda _path: default_config())
    monkeypatch.setattr(main_module, "InstanceRegistry", PeerRegistry)
    monkeypatch.setattr(main_module, "AmbientController", controller_must_not_load)

    with pytest.raises(SystemExit) as error:
        asyncio.run(main_module._main())

    assert error.value.code == 3
    assert closed
    assert "already running" in capsys.readouterr().err


def test_startup_lock_failure_short_circuits_before_heartbeat_or_models(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    closed = False

    class LockedRegistry:
        def claim_exclusive(self) -> bool:
            return False

        def heartbeat_and_count(self) -> int:
            raise AssertionError("heartbeat ran after the lifetime lock failed")

        def close(self) -> None:
            nonlocal closed
            closed = True

    def controller_must_not_load(*_args, **_kwargs):
        raise AssertionError("controller/model construction ran after lock failure")

    monkeypatch.setattr(main_module, "load_config", lambda _path: default_config())
    monkeypatch.setattr(main_module, "InstanceRegistry", LockedRegistry)
    monkeypatch.setattr(main_module, "AmbientController", controller_must_not_load)

    with pytest.raises(SystemExit) as error:
        asyncio.run(main_module._main())

    assert error.value.code == 3
    assert closed
    assert "application lock is held" in capsys.readouterr().err
