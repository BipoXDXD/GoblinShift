"""Install the official Firefox r1575 fix for Playwright 1.65.1 on Ubuntu CI.

Upstream microsoft/playwright#43982 fixes Mozilla 2090055. Keep the client pinned
until its stable browser bundle contains the fix. The archive is verified before
extraction, and the standard Playwright browser cache remains independently usable.
"""

import argparse
import hashlib
import os
import platform
import shutil
import tempfile
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile

URL = "https://cdn.playwright.dev/dbazure/download/playwright/builds/firefox/1575/firefox-ubuntu-24.04.zip"
SHA256 = "026c3861bbbb863848cb6ac3eb7ffdd6443dde790038c2832441e3edb71a2c74"
PREFIX = "gs-firefox-r1575-"


def install(runner_temp: Path, github_env: Path) -> Path:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("Firefox pin requires Ubuntu 24.04 AMD64")
    release = platform.freedesktop_os_release()
    if (release.get("ID"), release.get("VERSION_ID")) != ("ubuntu", "24.04"):
        raise ValueError("Firefox pin requires Ubuntu 24.04 AMD64")
    root = Path(tempfile.mkdtemp(prefix=PREFIX, dir=runner_temp)).resolve()
    try:
        archive = root / "firefox.zip"
        digest = hashlib.sha256()
        with urlopen(URL, timeout=120) as response, archive.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != SHA256:
            raise ValueError("Official Firefox archive SHA-256 mismatch")
        with ZipFile(archive) as bundle:
            # Every admitted byte belongs to the pinned official archive.
            bundle.extractall(root)
        executable = root / "firefox" / "firefox"
        if not executable.is_file():
            raise ValueError("Official Firefox executable missing")
        # Restore the archive's Unix file permissions, excluding privileged bits.
        with ZipFile(archive) as bundle:
            for member in bundle.infolist():
                file = root / member.filename
                if file.is_file():
                    mode = (member.external_attr >> 16) & 0o777
                    file.chmod(mode)
        if not os.access(executable, os.X_OK):
            raise ValueError("Official Firefox executable has no execute permission")
        archive.unlink()
        with github_env.open("a") as output:
            output.write(f"GS_FIREFOX_EXECUTABLE={executable}\n")
        return executable
    except BaseException:
        shutil.rmtree(root)
        raise


def cleanup(runner_temp: Path, executable: Path) -> None:
    root = executable.parent.parent
    if (
        root.parent.resolve() != runner_temp.resolve()
        or not root.name.startswith(PREFIX)
        or executable != root / "firefox" / "firefox"
        or root.is_symlink()
    ):
        raise ValueError("Refusing cleanup outside the owned Firefox directory")
    shutil.rmtree(root, ignore_errors=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    if args.cleanup:
        if value := os.environ.get("GS_FIREFOX_EXECUTABLE"):
            cleanup(runner_temp, Path(value))
    else:
        executable = install(runner_temp, Path(os.environ["GITHUB_ENV"]))
        print(f"Verified Firefox r1575 SHA-256 {SHA256}, executable {executable}")


if __name__ == "__main__":
    main()
