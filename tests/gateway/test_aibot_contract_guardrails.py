"""Guardrails for the frozen public AIBOT v1 contract."""

from __future__ import annotations

import json
from pathlib import Path
import re

from gateway.platforms.aibot_contract import build_public_contract_manifest

ROOT = Path(__file__).resolve().parents[2]
PLATFORMS_DIR = ROOT / "gateway" / "platforms"
BASELINE_PATH = PLATFORMS_DIR / "AIBOT_AGENT_API_V1_BASELINE.json"
COMMANDS_DOC_PATH = PLATFORMS_DIR / "AIBOT_COMMANDS.md"
GOVERNANCE_DOC_PATH = PLATFORMS_DIR / "AIBOT_PROTOCOL_GOVERNANCE.md"

DOC_DIRECTION_MAP = {
    "Client -> Server": "client_to_server",
    "Server -> Client": "server_to_client",
    "双向": "bidirectional",
}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_json(path: Path):
    return json.loads(_read_text(path))


def _slice_section(text: str, heading: str, stop_prefixes: tuple[str, ...]) -> list[str]:
    lines = text[text.index(heading) :].splitlines()[1:]
    collected: list[str] = []
    for line in lines:
        if line.startswith(stop_prefixes):
            break
        collected.append(line.rstrip())
    return collected


def _collect_backticked_bullets(text: str, heading: str, stop_prefixes: tuple[str, ...]) -> list[str]:
    section_lines = _slice_section(text, heading, stop_prefixes)
    items: list[str] = []
    started = False
    for line in section_lines:
        stripped = line.strip()
        match = re.match(r"- `([^`]+)`$", stripped)
        if match:
            started = True
            items.append(match.group(1))
            continue
        if started and stripped:
            break
    return items


def _extract_command_table(text: str) -> list[dict[str, str]]:
    section_lines = _slice_section(text, "## 3. 命令总表", ("## ",))
    commands: list[dict[str, str]] = []
    for line in section_lines:
        stripped = line.strip()
        if not stripped.startswith("| `"):
            continue
        cells = [cell.strip() for cell in stripped.split("|")[1:-1]]
        commands.append(
            {
                "cmd": cells[0].strip("`"),
                "direction": DOC_DIRECTION_MAP[cells[1]],
            }
        )
    return commands


def test_baseline_json_matches_public_contract_manifest():
    assert _load_json(BASELINE_PATH) == build_public_contract_manifest()


def test_commands_doc_matches_frozen_command_table():
    manifest = build_public_contract_manifest()
    expected = [
        {"cmd": entry["cmd"], "direction": entry["direction"]}
        for entry in manifest["public_commands"]
    ]

    assert _extract_command_table(_read_text(COMMANDS_DOC_PATH)) == expected


def test_commands_doc_lists_match_frozen_contract_values():
    manifest = build_public_contract_manifest()
    commands_doc = _read_text(COMMANDS_DOC_PATH)

    assert _collect_backticked_bullets(
        commands_doc,
        "以下字段名不是标准协议字段，不应出现在对外接口中：",
        ("## ",),
    ) == manifest["forbidden_public_fields"]
    assert _collect_backticked_bullets(
        commands_doc,
        "当前标准错误码：",
        ("## ", "### "),
    ) == manifest["error_codes"]
    assert _collect_backticked_bullets(
        commands_doc,
        "当前标准能力：",
        ("## ", "### "),
    ) == manifest["capabilities"]["stable"]
    assert _collect_backticked_bullets(
        commands_doc,
        "当前认证阶段要求至少声明：",
        ("## ", "### "),
    ) == manifest["capabilities"]["required"]


def test_governance_doc_matches_minimal_plugin_surface():
    manifest = build_public_contract_manifest()
    governance_doc = _read_text(GOVERNANCE_DOC_PATH)

    assert _collect_backticked_bullets(
        governance_doc,
        "插件侧为了长期稳定，建议只实现最小稳定面：",
        ("## ", "### "),
    ) == manifest["minimal_plugin_surface"]


def test_governance_doc_mentions_support_window_and_release_gates():
    governance_doc = _read_text(GOVERNANCE_DOC_PATH)

    assert "AIBOT_AGENT_API_V1_BASELINE.json" in governance_doc
    assert "`12` 个月" in governance_doc
    assert "旧插件基线回归必须通过" in governance_doc
