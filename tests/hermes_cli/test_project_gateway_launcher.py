"""Tests for the project gateway launcher."""

from pathlib import Path

import hermes_cli.project_gateway_launcher as launcher


def test_main_defaults_to_default_gateway(monkeypatch):
    started = []

    monkeypatch.setattr(
        launcher,
        "start_gateway",
        lambda profile=None: started.append(profile) or {},
    )

    assert launcher.main([]) == 0
    assert started == [None]


def test_main_can_start_default_and_profile(monkeypatch):
    started = []

    monkeypatch.setattr(
        launcher,
        "start_gateway",
        lambda profile=None: started.append(profile) or {},
    )

    assert launcher.main(["grix-agent", "--with-default"]) == 0
    assert started == [None, "grix-agent"]


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
    assert result["already_running"] is False
    assert result["hermes_home"] == str(expected_home)
