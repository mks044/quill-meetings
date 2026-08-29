#!/usr/bin/env python3
"""Install Quill's periodic Mac uploader as a launchd user agent."""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import tempfile
from pathlib import Path


LABEL = "com.digimata.quill-sync"


def build_plist(program: Path, state_dir: Path) -> dict:
    """Return a wake-safe five-minute sync schedule.

    StartCalendarInterval is intentional: unlike StartInterval, launchd
    coalesces calendar firings missed during sleep into one run on wake.
    """
    return {
        "Label": LABEL,
        "ProgramArguments": [str(program)],
        "RunAtLoad": True,
        "StartCalendarInterval": [{"Minute": minute} for minute in range(0, 60, 5)],
        "ProcessType": "Background",
        "ThrottleInterval": 30,
        "StandardOutPath": str(state_dir / "quill-sync-agent.out.log"),
        "StandardErrorPath": str(state_dir / "quill-sync-agent.err.log"),
    }


def write_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        tmp = Path(handle.name)
        plistlib.dump(value, handle, fmt=plistlib.FMT_XML, sort_keys=True)
    try:
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def install(program: Path, home: Path) -> Path:
    program = program.expanduser().resolve()
    if not program.is_file() or not os.access(program, os.X_OK):
        raise SystemExit(f"sync program is not executable: {program}")

    state_dir = home / ".local/state"
    state_dir.mkdir(parents=True, exist_ok=True)
    path = home / "Library/LaunchAgents" / f"{LABEL}.plist"
    write_atomic(path, build_plist(program, state_dir))

    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["/bin/launchctl", "bootout", domain, str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    result = subprocess.run(
        ["/bin/launchctl", "bootstrap", domain, str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise SystemExit(f"launchctl bootstrap failed: {result.stderr.strip()}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--program", type=Path, required=True)
    parser.add_argument("--home", type=Path, default=Path.home())
    args = parser.parse_args()
    path = install(args.program, args.home.expanduser().resolve())
    print(f"   scheduled sync -> {path}")


if __name__ == "__main__":
    main()
