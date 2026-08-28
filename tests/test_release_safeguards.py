"""Release files keep macOS installs deterministic and fail closed."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _locked_requirements() -> list[str]:
    text = (ROOT / "requirements-macos-arm64.txt").read_text(encoding="utf-8")
    logical = text.replace("\\\n", " ")
    return [
        line.strip()
        for line in logical.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_macos_lock_is_fully_pinned_and_hashed() -> None:
    requirements = _locked_requirements()
    assert requirements
    for requirement in requirements:
        assert re.match(r"^[A-Za-z0-9_.-]+==[^ ]+ ", requirement), requirement
        assert "--hash=sha256:" in requirement, requirement
    names = {item.split("==", 1)[0].casefold().replace("_", "-") for item in requirements}
    assert {"pytest", "sounddevice", "silero-vad", "torch"} <= names
    assert not any(name.startswith("nvidia-") for name in names)


def test_repository_normalizes_text_to_lf() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "* text=auto eol=lf" in attributes
    assert "*.sh text eol=lf" in attributes


def test_voice_assets_are_sha256_pinned_and_downloaded_atomically() -> None:
    launcher = (ROOT / "run-macos.sh").read_text(encoding="utf-8")
    assert "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5" in launcher
    assert "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d" in launcher
    assert "shasum -a 256" in launcher
    assert 'mv -f "$temporary" "$target"' in launcher
    assert "--proto-redir '=https'" in launcher
    assert "--connect-timeout 15" in launcher
    assert "--speed-limit 1024" in launcher
    assert "--speed-time 30" in launcher
    assert "curl --noproxy '*' --max-time 2 -sf" in launcher
    assert "concurrent launchers only remove their own mktemp path" in launcher
    assert "trap 'status=$?;" in launcher
    assert launcher.count('if [ "$status" -ge 128 ]; then') == 2


def test_macos_ollama_gate_uses_an_owned_private_listener() -> None:
    launcher = (ROOT / "run-macos.sh").read_text(encoding="utf-8")
    assert 's.bind(("127.0.0.1", 0))' in launcher
    assert "AMBIENTQA_REQUIRE_MANAGED_OLLAMA=1" in launcher
    assert 'export AMBIENTQA_OLLAMA_PID="$OLLAMA_PID"' in launcher
    assert "/usr/sbin/lsof" in launcher
    assert "-sTCP:LISTEN" in launcher
    assert "OLLAMA_NO_CLOUD=1" in launcher
    assert "OLLAMA_DEBUG_LOG_REQUESTS=0" in launcher
    assert 'ambientqa-ollama.log.XXXXXX' in launcher
    assert 'rm -f -- "$OLLAMA_LOG"' in launcher
    assert "Private Ollama exited; stopping Ambient" in launcher
    assert "http://127.0.0.1:11434/api/version" not in launcher


def test_setup_guards_intel_and_checks_espeak_fallback() -> None:
    setup = (ROOT / "setup-macos.sh").read_text(encoding="utf-8")
    assert "Intel macOS setup is disabled" in setup
    assert "--require-hashes" in setup
    assert "--only-binary=:all:" in setup
    assert "brew install espeak-ng" in setup
    assert '!= "RIFF"' in setup
    assert "requires macOS 14 or newer" in setup
    assert "scripts/fetch_whisper_model.py" in setup
    assert "HF_HUB_DISABLE_PROGRESS_BARS=1" in setup
    launcher = (ROOT / "run-macos.sh").read_text(encoding="utf-8")
    assert "requires macOS 14 or newer" in launcher
    revision = "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
    assert revision in launcher
    assert revision in (ROOT / "config.macos.toml").read_text(encoding="utf-8")
    assert "catches deletion/corruption" in launcher


def test_whisper_snapshot_and_weight_are_immutable_and_hash_verified() -> None:
    fetcher = (ROOT / "scripts" / "fetch_whisper_model.py").read_text(
        encoding="utf-8"
    )
    assert "mobiuslabsgmbh/faster-whisper-large-v3-turbo" in fetcher
    assert "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf" in fetcher
    assert "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da" in fetcher
    assert "os.rename(temporary, target)" in fetcher


def test_ci_uses_native_macos_15_runners_and_audits_the_lock() -> None:
    workflow = (ROOT / ".github/workflows/macos-release.yml").read_text(encoding="utf-8")
    assert "runs-on: macos-15\n" in workflow
    assert "runs-on: macos-15-intel\n" in workflow
    assert "./setup-macos.sh" in workflow
    assert "pip-audit" in workflow
    assert "requirements-macos-arm64.txt" in workflow
    assert workflow.count("persist-credentials: false") == 3
    assert "--requirement requirements-audit.txt" in workflow
    assert workflow.count("--require-hashes") >= 1
    assert workflow.count("--only-binary=:all:") >= 1


def test_ci_audit_tool_lock_is_fully_pinned_and_hashed() -> None:
    text = (ROOT / "requirements-audit.txt").read_text(encoding="utf-8")
    logical = text.replace("\\\n", " ")
    requirements = [
        line.strip()
        for line in logical.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert requirements
    assert any(item.casefold().startswith("pip-audit==2.10.1 ") for item in requirements)
    for requirement in requirements:
        assert re.match(r"^[A-Za-z0-9_.-]+==[^ ]+ ", requirement), requirement
        assert "--hash=sha256:" in requirement, requirement


def test_voice_download_interrupt_stops_launch_and_cleans_partial(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "run-macos.sh"
    shutil.copy2(ROOT / "run-macos.sh", launcher)
    lock = tmp_path / "requirements-macos-arm64.txt"
    lock.write_text("test lock\n", encoding="utf-8")
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()

    venv_bin = tmp_path / ".venv-macos" / "bin"
    venv_bin.mkdir(parents=True)
    calls = tmp_path / "calls"
    _write_executable(
        venv_bin / "python",
        "#!/bin/sh\n"
        'case "$1" in scripts/fetch_whisper_model.py) exit 0;; esac\n'
        'printf "%s\\n" python >>"$AMBIENT_TEST_CALLS"\n',
    )
    setup = tmp_path / "setup-macos.sh"
    _write_executable(setup, "#!/bin/sh\nexit 99\n")
    stamp = tmp_path / ".venv-macos" / ".deps-installed"
    stamp.write_text(f"{digest}\n", encoding="utf-8")
    newer = setup.stat().st_mtime + 2
    os.utime(stamp, (newer, newer))
    whisper = (
        tmp_path
        / "models"
        / "faster-whisper-large-v3-turbo-0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
    )
    whisper.mkdir(parents=True)
    (whisper / "model.bin").write_bytes(b"test")
    (whisper / ".revision").write_text(
        "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf\n", encoding="ascii"
    )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "uname",
        '#!/bin/sh\ncase "$1" in -s) echo Darwin;; -m) echo arm64;; esac\n',
    )
    _write_executable(fake_bin / "sw_vers", "#!/bin/sh\necho 14.7\n")
    _write_executable(fake_bin / "espeak-ng", "#!/bin/sh\nexit 0\n")
    _write_executable(
        fake_bin / "curl",
        '#!/bin/sh\nprintf "%s\\n" curl >>"$AMBIENT_TEST_CALLS"\nexit 130\n',
    )

    env = dict(os.environ)
    env["AMBIENT_TEST_CALLS"] = str(calls)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    completed = subprocess.run(
        [str(launcher), "--voice"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 130
    assert calls.read_text(encoding="utf-8").splitlines() == ["curl"]
    assert not list((tmp_path / "models").glob(".*"))
