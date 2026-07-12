#!/usr/bin/env python3
"""Ensure Camoufox browser binaries are installed."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

from platformdirs import user_cache_dir

VERSION = "152.0.4"
RELEASE = "zeta.1"


def install_dir() -> Path:
    return Path(user_cache_dir("camoufox"))


def launch_binary_name() -> str:
    if sys.platform == "darwin":
        return "Camoufox.app"
    if sys.platform == "win32":
        return "camoufox.exe"
    return "camoufox-bin"


def launch_binary_path() -> Path:
    base = install_dir()
    name = launch_binary_name()
    if sys.platform == "darwin":
        return base / name
    return base / name


def is_browser_installed() -> bool:
    path = launch_binary_path()
    if not path.exists():
        return False
    version_file = install_dir() / "version.json"
    return version_file.exists()


def run_camoufox_fetch() -> None:
    print("Installing Camoufox via `python -m camoufox fetch`...")
    subprocess.run([sys.executable, "-m", "camoufox", "fetch"], check=True)


def write_pinned_version_file() -> None:
    (install_dir() / "version.json").write_text(
        json.dumps({"version": VERSION, "release": RELEASE}),
        encoding="utf-8",
    )


def ensure_pinned_browser(force: bool = False) -> bool:
    if not force and is_browser_installed():
        print(f"Camoufox already installed at {install_dir()}")
        return False

    run_camoufox_fetch()

    if not is_browser_installed():
        raise RuntimeError(
            f"Camoufox fetch finished, but binary not found at {launch_binary_path()}"
        )

    write_pinned_version_file()
    print(f"Camoufox ready at {launch_binary_path()}")
    return True


if __name__ == "__main__":
  force = "--force" in sys.argv
  ensure_pinned_browser(force=force)
  print("platform", sys.platform, platform.machine(), "dir", install_dir())
