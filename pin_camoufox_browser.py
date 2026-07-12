#!/usr/bin/env python3
"""Install a known-good Camoufox build for ricardo.ch scraping."""

from __future__ import annotations

import io
import json
import os
import platform
import shlex
import sys
import zipfile
from pathlib import Path

import requests
from platformdirs import user_cache_dir

VERSION = "152.0.4"
RELEASE = "zeta.1"
ASSET_URLS = {
    ("darwin", "arm64"): (
        "https://github.com/daijro/camoufox/releases/download/v152.0.2-alpha/"
        "camoufox-152.0.4-alpha.25-mac.arm64.zip"
    ),
    ("linux", "x86_64"): (
        "https://github.com/daijro/camoufox/releases/download/v152.0.2-alpha/"
        "camoufox-152.0.4-alpha.25-lin.x86_64.zip"
    ),
}


def install_dir() -> Path:
    return Path(user_cache_dir("camoufox"))


def is_pinned_build_installed() -> bool:
    version_file = install_dir() / "version.json"
    if version_file.exists():
        try:
            data = json.loads(version_file.read_text(encoding="utf-8"))
            if data.get("version") == VERSION and data.get("release") == RELEASE:
                if sys.platform == "darwin":
                    return (install_dir() / "Camoufox.app").exists()
                return any(install_dir().iterdir())
        except json.JSONDecodeError:
            pass
    return any(install_dir().iterdir()) if install_dir().exists() else False


def install_pinned_build() -> None:
    machine = platform.machine().lower()
    if machine == "amd64":
        machine = "x86_64"
    key = (sys.platform, machine)
    url = ASSET_URLS.get(key)
    if not url:
        import subprocess

        print(f"No pinned asset for {key}, falling back to camoufox fetch")
        subprocess.run([sys.executable, "-m", "camoufox", "fetch"], check=True)
        return

    target = install_dir()
    print(f"Downloading Camoufox from {url}")
    response = requests.get(url, timeout=180)
    response.raise_for_status()

    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        archive.extractall(target)

    if sys.platform != "win32":
        os.system(f"chmod -R 755 {shlex.quote(str(target))}")  # nosec

    (target / "version.json").write_text(
        json.dumps({"version": VERSION, "release": RELEASE}),
        encoding="utf-8",
    )
    print(f"Pinned Camoufox to {VERSION}-{RELEASE}")


def ensure_pinned_browser(force: bool = False) -> bool:
    if not force and is_pinned_build_installed():
        return False
    install_pinned_build()
    return True


if __name__ == "__main__":
    ensure_pinned_browser(force="--force" in sys.argv)
