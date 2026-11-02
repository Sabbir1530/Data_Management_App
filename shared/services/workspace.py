"""Per-user / per-run workspace folders for MDM.

Each workspace is an isolated directory that holds:
  - cache/     AI Auto-Match resume checkpoints
  - output/    default location for nad_ai.xlsx / dist_ai.xlsx and other exports
  - config/    optional per-workspace settings (e.g. last connection)

Multiple users (or the same user running different jobs) pick or create a
workspace so their caches and outputs never collide.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

DEFAULT_WORKSPACE_NAME = "default"
STATE_FILE_NAME = "mdm_workspace_state.json"

def workspace_root() -> Path:
    """Base directory under which all workspaces live."""
    env = os.environ.get("MDM_WORKSPACE_ROOT")
    if env:
        root = Path(env)
    else:
        
        root = Path(__file__).resolve().parent.parent / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    return root

def _state_path() -> Path:
    """Global pointer to the last-used workspace (lives next to the root)."""
    return workspace_root() / STATE_FILE_NAME

def _load_state() -> dict:
    path = _state_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_state(data: dict) -> None:
    path = _state_path()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

def list_workspaces() -> list[str]:
    """Return sorted list of workspace folder names under the root."""
    root = workspace_root()
    names = []
    for p in root.iterdir():
        if p.is_dir() and not p.name.startswith("."):
            names.append(p.name)
    return sorted(names)

def get_active_workspace_name() -> str:
    state = _load_state()
    name = (state.get("active_workspace") or DEFAULT_WORKSPACE_NAME).strip()
    return name or DEFAULT_WORKSPACE_NAME

def set_active_workspace(name: str) -> Path:
    """Set the active workspace by name and ensure its subfolders exist."""
    name = (name or DEFAULT_WORKSPACE_NAME).strip()
    
    name = name.replace("\\", "/").split("/")[-1]
    if not name or name in (".", ".."):
        name = DEFAULT_WORKSPACE_NAME
    path = workspace_root() / name
    _ensure_layout(path)
    state = _load_state()
    state["active_workspace"] = name
    _save_state(state)
    return path

_custom_workspace_path: Optional[Path] = None

def set_custom_workspace_path(path: Path) -> None:
    global _custom_workspace_path
    _custom_workspace_path = Path(path).resolve()
    _ensure_layout(_custom_workspace_path)
    state = _load_state()
    state["custom_workspace_path"] = str(_custom_workspace_path)
    _save_state(state)

def _init_custom_path():
    global _custom_workspace_path
    state = _load_state()
    saved_path = state.get("custom_workspace_path")
    if saved_path:
        p = Path(saved_path)
        if p.exists():
            _custom_workspace_path = p

_init_custom_path()

def get_last_schema() -> str:
    return _load_state().get("last_schema", "")

def set_last_schema(schema: str) -> None:
    state = _load_state()
    state["last_schema"] = schema.strip()
    _save_state(state)

def get_workspace_path(name: Optional[str] = None) -> Path:
    """Return absolute path of the named (or active) workspace, creating layout if needed."""
    global _custom_workspace_path
    if _custom_workspace_path is not None and name is None:
        return _custom_workspace_path
    if name is None:
        name = get_active_workspace_name()
    path = workspace_root() / name
    _ensure_layout(path)
    return path

def _ensure_layout(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "cache").mkdir(exist_ok=True)
    (path / "output").mkdir(exist_ok=True)
    (path / "config").mkdir(exist_ok=True)

def cache_dir() -> Path:
    """AI cache directory for the active workspace."""
    return get_workspace_path() / "cache"

def output_dir() -> Path:
    """Default export directory for the active workspace."""
    return get_workspace_path() / "output"

def config_dir() -> Path:
    """Per-workspace config directory."""
    return get_workspace_path() / "config"

def global_cache_dir() -> Path:
    """Global cache directory for cross-workspace files like company master or login config."""
    path = Path(__file__).resolve().parent.parent / "cache" / "global"
    path.mkdir(parents=True, exist_ok=True)
    return path

def connection_file_path() -> Path:
    """Where to store the saved login globally."""
    return global_cache_dir() / "mdm_connection.json"

def create_workspace(name: str) -> Path:
    """Create a new workspace and switch to it."""
    return set_active_workspace(name)


set_active_workspace(get_active_workspace_name())
