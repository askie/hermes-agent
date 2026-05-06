"""Grix create-folder local action handler.

Implements the create_folder local action protocol so the frontend can
create directories on the agent's filesystem through the grix WebSocket channel.
"""

from __future__ import annotations

import os
import platform
import stat as stat_mod
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from gateway.platforms.grix_file_list import real_home_dir


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def handle_create_folder_action(
    params: Dict[str, Any],
    *,
    resolve_cwd: Callable[[str], Optional[str]],
    fallback_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Handle a ``create_folder`` local action.

    Returns a dict matching the ``local_action_result`` payload:
    - ``{status: "ok", result: {id: "...", name: "...", is_directory: true, ...}}``
    - ``{status: "failed", error_code: "...", error_msg: "..."}``
    """
    parent_id = params.get("parent_id") or None
    name = params.get("name") or ""
    session_id = params.get("session_id") or ""

    # Validate name.
    if not name.strip():
        return _fail("invalid_name", "Folder name must not be empty")

    # Reject path separators in the name.
    if os.sep in name or (os.altsep and os.altsep in name):
        return _fail("invalid_name", "Folder name must not contain path separators")

    cwd = resolve_cwd(session_id) if session_id else None
    target_parent = parent_id or cwd or fallback_dir or real_home_dir()

    if not target_parent:
        return _fail("path_not_found", "No parent directory resolved")

    # Resolve symlinks on parent.
    try:
        real_parent = os.path.realpath(target_parent)
    except OSError:
        return _fail("path_not_found", f"Parent directory not found: {target_parent}")

    # If cwd is set, restrict to within cwd.
    if cwd:
        try:
            real_cwd = os.path.realpath(cwd)
        except OSError:
            real_cwd = None
        if real_cwd and not _is_within_path(real_parent, real_cwd):
            return _fail(
                "path_outside_cwd",
                "Parent path is outside session working directory",
            )

    # Verify parent is an existing directory.
    try:
        st = os.stat(real_parent)
        if not stat_mod.S_ISDIR(st.st_mode):
            return _fail("not_a_directory", f"Parent path is not a directory: {target_parent}")
    except PermissionError:
        return _fail("path_not_accessible", f"Cannot access parent path: {target_parent}")
    except OSError:
        return _fail("path_not_found", f"Parent directory not found: {target_parent}")

    folder_path = os.path.join(real_parent, name)

    # Create the folder.
    try:
        os.mkdir(folder_path)
    except FileExistsError:
        return _fail("already_exists", f"Path already exists: {folder_path}")
    except PermissionError:
        return _fail("permission_denied", f"Permission denied creating folder: {folder_path}")
    except OSError as exc:
        return _fail("create_failed", str(exc))

    # Stat the newly created folder to return metadata.
    try:
        folder_st = os.stat(folder_path)
        modified_at = _isoformat(folder_st.st_mtime)
    except OSError:
        modified_at = None

    return {
        "status": "ok",
        "result": {
            "id": folder_path,
            "name": name,
            "is_directory": True,
            **({"modified_at": modified_at} if modified_at else {}),
        },
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_within_path(target: str, ancestor: str) -> bool:
    if target == ancestor:
        return True
    sep = os.sep
    norm_target = target if target.endswith(sep) else target + sep
    norm_ancestor = ancestor if ancestor.endswith(sep) else ancestor + sep
    return norm_target.startswith(norm_ancestor)


def _isoformat(timestamp: float) -> str:
    from datetime import datetime, timezone

    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _fail(error_code: str, error_msg: str) -> Dict[str, str]:
    return {"status": "failed", "error_code": error_code, "error_msg": error_msg}
