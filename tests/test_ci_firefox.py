"""The Firefox pin admits only the exact official archive and publishes env last."""

import hashlib
import platform
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

from scripts import ci_firefox


def archive() -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as bundle:
        entry = ZipInfo("firefox/firefox")
        entry.external_attr = 0o100755 << 16
        bundle.writestr(entry, b"synthetic executable")
    return output.getvalue()


def setup_download(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        platform,
        "freedesktop_os_release",
        lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"},
    )
    monkeypatch.setattr(ci_firefox, "urlopen", lambda *args, **kwargs: BytesIO(payload))


def test_pin_verifies_before_extraction_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_download(monkeypatch, archive())
    env = tmp_path / "github-env"
    with pytest.raises(ValueError, match="SHA-256"):
        ci_firefox.install(tmp_path, env)
    assert not env.exists()
    assert list(tmp_path.iterdir()) == []


def test_success_exports_executable_and_cleanup_owns_only_its_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = archive()
    setup_download(monkeypatch, payload)
    monkeypatch.setattr(ci_firefox, "SHA256", hashlib.sha256(payload).hexdigest())
    env = tmp_path / "github-env"
    other = tmp_path / "firefox-1572"
    other.mkdir()
    executable = ci_firefox.install(tmp_path, env)
    assert executable.read_bytes() == b"synthetic executable"
    assert executable.stat().st_mode & 0o111
    assert env.read_text() == f"GS_FIREFOX_EXECUTABLE={executable}\n"
    ci_firefox.cleanup(tmp_path, executable)
    assert other.is_dir()
    assert not executable.parent.parent.exists()


@pytest.mark.parametrize("payload", [b"not a zip", b""])
def test_matching_hash_cannot_admit_invalid_archive(
    payload: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zipfile import BadZipFile

    setup_download(monkeypatch, payload)
    monkeypatch.setattr(ci_firefox, "SHA256", hashlib.sha256(payload).hexdigest())
    with pytest.raises(BadZipFile):
        ci_firefox.install(tmp_path, tmp_path / "env")
    assert list(tmp_path.iterdir()) == []


def test_empty_archive_cannot_export_missing_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = BytesIO()
    with ZipFile(output, "w"):
        pass
    payload = output.getvalue()
    setup_download(monkeypatch, payload)
    monkeypatch.setattr(ci_firefox, "SHA256", hashlib.sha256(payload).hexdigest())
    with pytest.raises(ValueError, match="missing"):
        ci_firefox.install(tmp_path, tmp_path / "env")
    assert list(tmp_path.iterdir()) == []


def test_both_read_jobs_install_verify_and_cleanup_the_fixed_browser() -> None:
    import yaml

    root = Path(__file__).resolve().parents[1]
    for name, job_id in (("ci.yml", "vps-smoke"), ("e2e-vps.yml", "vps")):
        steps = yaml.safe_load((root / ".github/workflows" / name).read_text())["jobs"][job_id][
            "steps"
        ]
        commands = [step.get("run", "") for step in steps]
        install = commands.index("python3 scripts/ci_firefox.py")
        proof = commands.index("node scripts/check-firefox-aria.ts")
        cleanup = commands.index("python3 scripts/ci_firefox.py --cleanup")
        journey = next(
            i
            for i, step in enumerate(steps)
            if step.get("name")
            in {"Jornadas de leitura", "Borda e jornadas de leitura com a pilha descartável"}
        )
        assert install < proof < journey < cleanup
        assert steps[cleanup]["if"] == "always()"
    ci = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())["jobs"]["vps-smoke"]
    runs = [step.get("run", "") for step in ci["steps"]]
    assert runs.index("node scripts/check-firefox-aria.ts --expect-crash") < runs.index(
        "python3 scripts/ci_firefox.py"
    )


@pytest.mark.parametrize("host", ["Darwin", "Linux-arm64", "Ubuntu-22.04"])
def test_unsupported_host_fails_before_download(
    host: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_download(monkeypatch, archive())
    if host == "Darwin":
        monkeypatch.setattr(platform, "system", lambda: host)
    elif host == "Linux-arm64":
        monkeypatch.setattr(platform, "machine", lambda: "aarch64")
    else:
        monkeypatch.setattr(
            platform,
            "freedesktop_os_release",
            lambda: {"ID": "ubuntu", "VERSION_ID": "22.04"},
        )
    monkeypatch.setattr(ci_firefox, "urlopen", lambda *args, **kwargs: pytest.fail("download"))
    with pytest.raises(ValueError, match="Ubuntu"):
        ci_firefox.install(tmp_path, tmp_path / "env")
    assert list(tmp_path.iterdir()) == []


def test_broken_download_cleans_up_without_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_download(monkeypatch, b"")

    def fail(*args: object, **kwargs: object) -> BytesIO:
        raise OSError("interrupted download")

    monkeypatch.setattr(ci_firefox, "urlopen", fail)
    with pytest.raises(OSError, match="interrupted"):
        ci_firefox.install(tmp_path, tmp_path / "env")
    assert list(tmp_path.iterdir()) == []


def test_cleanup_refuses_other_directories(tmp_path: Path) -> None:
    other = tmp_path / "firefox-1572" / "firefox"
    other.mkdir(parents=True)
    with pytest.raises(ValueError, match="directory"):
        ci_firefox.cleanup(tmp_path, other / "firefox")
    assert other.is_dir()
