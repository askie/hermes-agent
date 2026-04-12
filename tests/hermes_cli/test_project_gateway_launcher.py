"""Tests for the project gateway launcher."""

from pathlib import Path

import hermes_cli.project_gateway_launcher as launcher


def test_main_defaults_to_default_and_paired_profile(monkeypatch):
    restarted = []

    monkeypatch.setattr(
        launcher,
        "restart_gateways",
        lambda targets: restarted.append(targets) or [],
    )

    assert launcher.main([]) == 0
    assert restarted == [[None, "grix-agent"]]


def test_main_with_profile_restarts_only_named_target(monkeypatch):
    restarted = []

    monkeypatch.setattr(
        launcher,
        "restart_gateways",
        lambda targets: restarted.append(targets) or [],
    )

    assert launcher.main(["grix-agent"]) == 0
    assert restarted == [["grix-agent"]]


def test_build_targets_uses_override_for_paired_profile(monkeypatch):
    monkeypatch.setenv("HERMES_PROJECT_GATEWAY_PROFILE", "demo-agent")

    assert launcher.build_targets(None) == [None, "demo-agent"]


def test_restart_gateways_stops_all_targets_before_starting(monkeypatch):
    events = []

    monkeypatch.setattr(
        launcher,
        "stop_gateway",
        lambda profile=None: events.append(("stop", profile)) or {},
    )
    monkeypatch.setattr(
        launcher,
        "start_gateway",
        lambda profile=None: events.append(("start", profile)) or {},
    )

    assert launcher.restart_gateways([None, "grix-agent"]) == [{}, {}]
    assert events == [
        ("stop", None),
        ("stop", "grix-agent"),
        ("start", None),
        ("start", "grix-agent"),
    ]


def test_start_gateway_uses_explicit_profile_home(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    captured = {}

    class FakePopen:
        def __init__(
            self,
            command,
            *,
            cwd,
            env,
            stdout,
            stderr,
            start_new_session,
        ):
            captured["command"] = command
            captured["cwd"] = cwd
            captured["env"] = env
            captured["stderr"] = stderr
            captured["start_new_session"] = start_new_session
            captured["stdout_name"] = stdout.name
            self.pid = 43210

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(launcher, "PROJECT_ROOT", repo_root)
    monkeypatch.setattr(launcher, "get_project_python_path", lambda: "/fake/python")
    monkeypatch.setattr(launcher, "gateway_is_running", lambda home: False)
    monkeypatch.setattr(
        launcher,
        "wait_for_gateway_running",
        lambda home, timeout_seconds=launcher.START_TIMEOUT_SECONDS: {
            "gateway_state": "running"
        },
    )
    monkeypatch.setattr(launcher.subprocess, "Popen", FakePopen)

    result = launcher.start_gateway("grix-agent")

    expected_home = tmp_path / ".hermes" / "profiles" / "grix-agent"
    expected_log = expected_home / "logs" / "project-gateway-launcher.log"

    assert captured["command"] == [
        "/fake/python",
        "-m",
        "hermes_cli.main",
        "gateway",
        "run",
    ]
    assert captured["cwd"] == str(repo_root)
    assert captured["env"]["HERMES_HOME"] == str(expected_home)
    assert captured["stderr"] == launcher.subprocess.STDOUT
    assert captured["start_new_session"] is True
    assert captured["stdout_name"] == str(expected_log)
    assert result["hermes_home"] == str(expected_home)


def test_stop_gateway_terminates_existing_process(monkeypatch, tmp_path):
    expected_home = tmp_path / ".hermes"
    pid_path = expected_home / "gateway.pid"
    state_path = expected_home / "gateway_state.json"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text("12345", encoding="utf-8")
    state_path.write_text('{"pid": 12345, "gateway_state": "running"}', encoding="utf-8")

    signals = []
    running = {"active": True}

    def fake_kill(pid, sig):
        signals.append((pid, sig))
        if sig == launcher.signal.SIGTERM:
            running["active"] = False

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(launcher.os, "kill", fake_kill)
    monkeypatch.setattr(
        launcher,
        "process_is_running",
        lambda pid: running["active"],
    )

    result = launcher.stop_gateway()

    assert signals == [(12345, launcher.signal.SIGTERM)]
    assert result["was_running"] is True
    assert result["pid"] == 12345
    assert not pid_path.exists()
    assert not state_path.exists()
