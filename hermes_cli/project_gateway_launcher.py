"""Project-scoped launcher for restarting and provisioning Hermes gateways.

This launcher keeps a small list of named profiles that belong to this project:

- no arguments restart the default gateway plus every managed named profile
- a profile name restarts only ``~/.hermes/profiles/<name>``
- ``add`` creates or updates a named profile with its own Grix credentials,
  registers it for future restarts, and starts that profile immediately

It does not reimplement gateway behavior; it only launches the existing
``hermes_cli.main gateway run`` entry point with an explicit ``HERMES_HOME``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DEFAULT_PROFILE_ENV_VAR = "HERMES_PROJECT_GATEWAY_PROFILE"
DEFAULT_PROFILE_NAME = "grix-agent"
MANAGED_PROFILES_FILE = "project_gateway_profiles.json"
START_TIMEOUT_SECONDS = 20.0
STOP_TIMEOUT_SECONDS = 10.0
FORCE_KILL_AFTER_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.25
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


def get_default_profile_name() -> str:
    """Return the paired profile used when no explicit target is provided."""
    configured = str(os.getenv(DEFAULT_PROFILE_ENV_VAR, "")).strip()
    if configured:
        return validate_profile_name(configured)
    return DEFAULT_PROFILE_NAME


def _gateway_pid_path(hermes_home: Path) -> Path:
    return hermes_home / "gateway.pid"


def _gateway_state_path(hermes_home: Path) -> Path:
    return hermes_home / "gateway_state.json"


def _launcher_log_path(hermes_home: Path) -> Path:
    return hermes_home / "logs" / "project-gateway-launcher.log"


def _managed_profiles_path() -> Path:
    return resolve_hermes_home() / MANAGED_PROFILES_FILE


def _remove_runtime_files(hermes_home: Path) -> None:
    for path in (_gateway_pid_path(hermes_home), _gateway_state_path(hermes_home)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


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


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        suffix=".tmp",
        prefix=path.name + ".",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_managed_profiles() -> list[str]:
    """Read the persisted ordered list of managed named profiles."""
    payload = _read_json_file(_managed_profiles_path())
    if not payload:
        return []

    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, list):
        return []

    normalized: list[str] = []
    for entry in raw_profiles:
        if not isinstance(entry, str):
            continue
        try:
            name = validate_profile_name(entry)
        except ValueError:
            continue
        if name not in normalized:
            normalized.append(name)
    return normalized


def write_managed_profiles(profiles: list[str]) -> list[str]:
    """Persist the ordered list of managed named profiles."""
    normalized: list[str] = []
    for profile in profiles:
        name = validate_profile_name(profile)
        if name not in normalized:
            normalized.append(name)
    _write_json_file(_managed_profiles_path(), {"profiles": normalized})
    return normalized


def get_managed_profiles() -> list[str]:
    """Return managed profiles, preserving legacy single-profile behavior."""
    managed_path = _managed_profiles_path()
    configured = read_managed_profiles()
    if configured or managed_path.exists():
        return configured

    legacy = get_default_profile_name()
    legacy_home = resolve_hermes_home(legacy)
    if legacy_home.is_dir():
        return [legacy]
    return []


def register_managed_profile(profile: str) -> list[str]:
    """Add *profile* to the managed profile list if needed."""
    name = validate_profile_name(profile)
    profiles = get_managed_profiles()
    if name not in profiles:
        profiles.append(name)
    return write_managed_profiles(profiles)


def _read_env_lines(env_path: Path) -> list[str]:
    if not env_path.exists():
        return []
    return env_path.read_text(encoding="utf-8").splitlines(keepends=True)


def _write_env_lines(env_path: Path, lines: list[str]) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(env_path.parent),
        suffix=".tmp",
        prefix=".env_",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, env_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass


def _set_env_value(env_path: Path, key: str, value: str) -> None:
    if not _ENV_VAR_NAME_RE.fullmatch(key):
        raise ValueError(f"invalid env key: {key!r}")
    clean_value = value.replace("\n", "").replace("\r", "")
    lines = _read_env_lines(env_path)
    updated = False
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            lines[idx] = f"{key}={clean_value}\n"
            updated = True
            break
    if not updated:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{key}={clean_value}\n")
    _write_env_lines(env_path, lines)


def _remove_env_value(env_path: Path, key: str) -> None:
    if not _ENV_VAR_NAME_RE.fullmatch(key):
        raise ValueError(f"invalid env key: {key!r}")
    if not env_path.exists():
        return
    lines = _read_env_lines(env_path)
    filtered = [
        line for line in lines
        if not line.strip().startswith(f"{key}=")
    ]
    if filtered != lines:
        _write_env_lines(env_path, filtered)


def _default_clone_source() -> str:
    legacy = get_default_profile_name()
    if legacy and resolve_hermes_home(legacy).is_dir():
        return legacy
    return "default"


def _copy_profile_auth(source_dir: Path, profile_dir: Path) -> None:
    for filename in ("auth.json", "auth.lock"):
        src = source_dir / filename
        if src.exists():
            shutil.copy2(src, profile_dir / filename)


def ensure_profile(profile: str, clone_from: str | None = None) -> tuple[Path, bool]:
    """Ensure a named profile exists, cloning a working template when needed."""
    from hermes_cli.profiles import (
        create_profile,
        get_profile_dir,
        profile_exists,
        seed_profile_skills,
    )

    name = validate_profile_name(profile)
    if name == "default":
        raise ValueError("add only supports named profiles, not 'default'")

    if profile_exists(name):
        return get_profile_dir(name), False

    source_name = validate_profile_name(clone_from or _default_clone_source())
    source_dir = get_profile_dir(source_name)
    profile_dir = create_profile(
        name=name,
        clone_from=source_name,
        clone_config=True,
        no_alias=True,
    )
    _copy_profile_auth(source_dir, profile_dir)

    try:
        from plugins.memory.honcho.cli import clone_honcho_for_profile
        clone_honcho_for_profile(name)
    except Exception:
        pass

    try:
        seed_profile_skills(profile_dir, quiet=True)
    except Exception:
        pass

    return profile_dir, True


def configure_grix_profile(
    profile_dir: Path,
    *,
    endpoint: str,
    agent_id: str,
    api_key: str,
    allowed_users: str | None = None,
    allow_all_users: bool = False,
    home_channel: str | None = None,
    home_channel_name: str | None = None,
) -> None:
    """Write profile-local Grix credentials and routing settings."""
    env_path = profile_dir / ".env"
    _set_env_value(env_path, "GRIX_ENDPOINT", endpoint)
    _set_env_value(env_path, "GRIX_AGENT_ID", agent_id)
    _set_env_value(env_path, "GRIX_API_KEY", api_key)

    if allow_all_users:
        _remove_env_value(env_path, "GRIX_ALLOWED_USERS")
        _set_env_value(env_path, "GRIX_ALLOW_ALL_USERS", "true")
    elif allowed_users:
        _set_env_value(env_path, "GRIX_ALLOWED_USERS", allowed_users)
        _remove_env_value(env_path, "GRIX_ALLOW_ALL_USERS")
    else:
        _remove_env_value(env_path, "GRIX_ALLOWED_USERS")
        _remove_env_value(env_path, "GRIX_ALLOW_ALL_USERS")

    if home_channel:
        _set_env_value(env_path, "GRIX_HOME_CHANNEL", home_channel)
        if home_channel_name:
            _set_env_value(env_path, "GRIX_HOME_CHANNEL_NAME", home_channel_name)
        else:
            _remove_env_value(env_path, "GRIX_HOME_CHANNEL_NAME")
    else:
        _remove_env_value(env_path, "GRIX_HOME_CHANNEL")
        _remove_env_value(env_path, "GRIX_HOME_CHANNEL_NAME")


def add_managed_profile(
    profile: str,
    *,
    endpoint: str,
    agent_id: str,
    api_key: str,
    clone_from: str | None = None,
    allowed_users: str | None = None,
    allow_all_users: bool = False,
    home_channel: str | None = None,
    home_channel_name: str | None = None,
) -> dict[str, Any]:
    """Create or update one managed profile, register it, and restart it."""
    if home_channel_name and not home_channel:
        raise ValueError("--home-channel-name requires --home-channel")

    profile_dir, created = ensure_profile(profile, clone_from=clone_from)
    configure_grix_profile(
        profile_dir,
        endpoint=endpoint,
        agent_id=agent_id,
        api_key=api_key,
        allowed_users=allowed_users,
        allow_all_users=allow_all_users,
        home_channel=home_channel,
        home_channel_name=home_channel_name,
    )
    managed_profiles = register_managed_profile(profile)
    start_result = restart_gateways([profile])[0]
    start_result.update({
        "created": created,
        "managed_profiles": managed_profiles,
    })
    return start_result


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


def read_gateway_runtime_pid(hermes_home: Path) -> int | None:
    """Read the runtime PID from the PID file, or fall back to runtime state."""
    pid = read_gateway_pid(hermes_home)
    if pid is not None:
        return pid

    state = read_gateway_state(hermes_home) or {}
    state_pid = state.get("pid")
    return state_pid if isinstance(state_pid, int) and state_pid > 0 else None


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
    """Start the target gateway and return a small status dict."""
    hermes_home = resolve_hermes_home(profile)
    label = _target_label(profile)
    log_path = _launcher_log_path(hermes_home)

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
        "state": state,
    }


def stop_gateway(profile: str | None = None) -> dict[str, Any]:
    """Stop the target gateway when it is already running."""
    hermes_home = resolve_hermes_home(profile)
    label = _target_label(profile)
    pid = read_gateway_runtime_pid(hermes_home)

    if pid is None or not process_is_running(pid):
        _remove_runtime_files(hermes_home)
        return {
            "label": label,
            "hermes_home": str(hermes_home),
            "was_running": False,
        }

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _remove_runtime_files(hermes_home)
        return {
            "label": label,
            "hermes_home": str(hermes_home),
            "was_running": False,
        }

    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    force_deadline = time.monotonic() + FORCE_KILL_AFTER_SECONDS
    force_sent = False

    while time.monotonic() < deadline:
        if not process_is_running(pid):
            _remove_runtime_files(hermes_home)
            print(f"stopped existing {label} gateway")
            print(f"  HERMES_HOME: {hermes_home}")
            return {
                "label": label,
                "hermes_home": str(hermes_home),
                "was_running": True,
                "pid": pid,
            }

        if not force_sent and time.monotonic() >= force_deadline:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                _remove_runtime_files(hermes_home)
                print(f"stopped existing {label} gateway")
                print(f"  HERMES_HOME: {hermes_home}")
                return {
                    "label": label,
                    "hermes_home": str(hermes_home),
                    "was_running": True,
                    "pid": pid,
                }
            force_sent = True

        time.sleep(POLL_INTERVAL_SECONDS)

    raise RuntimeError(
        f"timed out while stopping {label} gateway (HERMES_HOME={hermes_home}, PID={pid})"
    )


def restart_gateways(targets: list[str | None]) -> list[dict[str, Any]]:
    """Restart every target, stopping all of them before any new start."""
    normalized_targets: list[str | None] = []
    for target in targets:
        normalized_targets.append(None if target is None else validate_profile_name(target))

    for target in normalized_targets:
        stop_gateway(target)

    results = []
    for target in normalized_targets:
        results.append(start_gateway(target))
    return results


def build_targets(profile: str | None) -> list[str | None]:
    """Return the ordered list of gateway targets to restart."""
    if profile:
        return [validate_profile_name(profile)]
    return [None, *get_managed_profiles()]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] == "add":
        parser = argparse.ArgumentParser(
            description=(
                "Create or update a managed Hermes profile with its own "
                "Grix credentials, then start it."
            )
        )
        parser.add_argument("command")
        parser.add_argument("profile", help="Named profile to create or update.")
        parser.add_argument("--endpoint", required=True, help="Grix websocket endpoint.")
        parser.add_argument("--agent-id", required=True, help="Grix agent ID.")
        parser.add_argument("--api-key", required=True, help="Grix API key.")
        parser.add_argument(
            "--clone-from",
            help=(
                "Existing profile to clone as the starting template. "
                f"Defaults to '{_default_clone_source()}'."
            ),
        )
        access_group = parser.add_mutually_exclusive_group()
        access_group.add_argument(
            "--allowed-users",
            help="Comma-separated sender_id allowlist for this profile.",
        )
        access_group.add_argument(
            "--allow-all-users",
            action="store_true",
            help="Allow all inbound Grix users for this profile.",
        )
        parser.add_argument(
            "--home-channel",
            help="Default Grix session_id for cron and send_message().",
        )
        parser.add_argument(
            "--home-channel-name",
            help="Display name paired with --home-channel.",
        )
        return parser.parse_args(argv)

    parser = argparse.ArgumentParser(
        description=(
            "Restart Hermes gateways from the current project for the default "
            "home or for a named profile."
        )
    )
    parser.add_argument(
        "profile",
        nargs="?",
        help="Profile name to restart. Omit to restart default and managed gateways.",
    )
    parser.set_defaults(command="restart")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "add":
        result = add_managed_profile(
            args.profile,
            endpoint=args.endpoint,
            agent_id=args.agent_id,
            api_key=args.api_key,
            clone_from=args.clone_from,
            allowed_users=args.allowed_users,
            allow_all_users=args.allow_all_users,
            home_channel=args.home_channel,
            home_channel_name=args.home_channel_name,
        )
        action = "created" if result.get("created") else "updated"
        print(f"{action} managed profile '{args.profile}'")
        print(f"  HERMES_HOME: {result['hermes_home']}")
        print(f"  Managed profiles: {', '.join(result['managed_profiles'])}")
        print("  Future `hermes-project-gateway` runs will restart this profile automatically.")
        return 0

    restart_gateways(build_targets(args.profile))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
