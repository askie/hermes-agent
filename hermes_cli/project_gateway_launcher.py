"""Start Hermes gateways from this project for the default home or a profile.

This launcher is intentionally small and focused:

- no profile name means the default ``~/.hermes`` gateway
- a profile name means ``~/.hermes/profiles/<name>``
- ``--with-default`` starts both the default gateway and the named profile

It does not reimplement gateway behavior; it only launches the existing
``hermes_cli.main gateway run`` entry point with an explicit ``HERMES_HOME``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
START_TIMEOUT_SECONDS = 20.0
POLL_INTERVAL_SECONDS = 0.25


def get_project_python_path() -> str:
    """Return the Python interpreter from the current project when available."""
    for candidate in (
        PROJECT_ROOT / "venv" / "bin" / "python",
        PROJECT_ROOT / ".venv" / "bin" / "python",
    ):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def validate_profile_name(profile: str) -> str:
    """Return a normalized profile name or raise a helpful error."""
    normalized = str(profile or "").strip()
    if not normalized:
        raise ValueError("profile name cannot be empty")
    if not PROFILE_NAME_RE.fullmatch(normalized):
        raise ValueError(
            "profile name must match ^[a-z0-9][a-z0-9_-]{0,63}$"
        )
    return normalized


def resolve_hermes_home(profile: str | None = None) -> Path:
    """Resolve the target HERMES_HOME for the default home or a named profile."""
    root = Path.home() / ".hermes"
    if not profile:
        return root
    return root / "profiles" / validate_profile_name(profile)


def _gateway_pid_path(hermes_home: Path) -> Path:
    return hermes_home / "gateway.pid"


def _gateway_state_path(hermes_home: Path) -> Path:
    return hermes_home / "gateway_state.json"


def _launcher_log_path(hermes_home: Path) -> Path:
    return hermes_home / "logs" / "project-gateway-launcher.log"


def _read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def read_gateway_pid(hermes_home: Path) -> int | None:
    """Read the gateway PID for the target home."""
    path = _gateway_pid_path(hermes_home)
    if not path.exists():
        return None

    payload = _read_json_file(path)
    if payload is not None:
        pid = payload.get("pid")
        return pid if isinstance(pid, int) and pid > 0 else None

    try:
        raw = path.read_text(encoding="utf-8").strip()
        pid = int(raw)
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def process_is_running(pid: int | None) -> bool:
    """Return True when *pid* refers to a live process."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def read_gateway_state(hermes_home: Path) -> dict[str, Any] | None:
    """Read the target gateway runtime state file."""
    return _read_json_file(_gateway_state_path(hermes_home))


def gateway_is_running(hermes_home: Path) -> bool:
    """Return True when the target gateway is already running."""
    state = read_gateway_state(hermes_home) or {}
    pid = read_gateway_pid(hermes_home)
    if state.get("gateway_state") == "running" and (
        pid is None or process_is_running(pid)
    ):
        return True
    return process_is_running(pid)


def wait_for_gateway_running(
    hermes_home: Path,
    timeout_seconds: float = START_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """Wait until the target gateway reports a running state."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = read_gateway_state(hermes_home)
        if state and state.get("gateway_state") == "running":
            pid = read_gateway_pid(hermes_home)
            if pid is None or process_is_running(pid):
                return state

        pid = read_gateway_pid(hermes_home)
        if pid is not None and process_is_running(pid):
            return state or {"gateway_state": "running", "pid": pid}

        time.sleep(POLL_INTERVAL_SECONDS)
    return None


def _spawn_gateway(hermes_home: Path) -> int:
    """Spawn a detached gateway process bound to *hermes_home*."""
    hermes_home.mkdir(parents=True, exist_ok=True)
    log_path = _launcher_log_path(hermes_home)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)

    command = [
        get_project_python_path(),
        "-m",
        "hermes_cli.main",
        "gateway",
        "run",
    ]

    log_handle = open(log_path, "ab")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception:
        log_handle.close()
        raise

    return process.pid


def _target_label(profile: str | None) -> str:
    return "default" if not profile else f"profile '{profile}'"


def start_gateway(profile: str | None = None) -> dict[str, Any]:
    """Ensure the target gateway is running and return a small status dict."""
    hermes_home = resolve_hermes_home(profile)
    label = _target_label(profile)
    log_path = _launcher_log_path(hermes_home)

    if gateway_is_running(hermes_home):
        print(f"{label} gateway is already running")
        print(f"  HERMES_HOME: {hermes_home}")
        print(f"  Log: {log_path}")
        return {
            "label": label,
            "hermes_home": str(hermes_home),
            "log_path": str(log_path),
            "already_running": True,
        }

    pid = _spawn_gateway(hermes_home)
    state = wait_for_gateway_running(hermes_home)
    if state is None:
        raise RuntimeError(
            f"timed out while starting {label} gateway (HERMES_HOME={hermes_home})"
        )

    print(f"started {label} gateway")
    print(f"  HERMES_HOME: {hermes_home}")
    print(f"  Spawn PID: {pid}")
    print(f"  Log: {log_path}")
    return {
        "label": label,
        "hermes_home": str(hermes_home),
        "log_path": str(log_path),
        "already_running": False,
        "state": state,
    }


def build_targets(profile: str | None, with_default: bool) -> list[str | None]:
    """Return the ordered list of gateway targets to start."""
    targets: list[str | None] = []
    if with_default or not profile:
        targets.append(None)
    if profile:
        targets.append(validate_profile_name(profile))
    return targets


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Start Hermes gateways from the current project for the default "
            "home or for a named profile."
        )
    )
    parser.add_argument(
        "profile",
        nargs="?",
        help="Profile name to start. Omit to start the default gateway.",
    )
    parser.add_argument(
        "--with-default",
        action="store_true",
        help="When starting a profile, start the default gateway too.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    targets = build_targets(args.profile, args.with_default)
    for target in targets:
        start_gateway(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
