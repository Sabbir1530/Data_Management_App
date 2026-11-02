"""Persist connection settings so login is only required the first time."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from shared.models.connection import ConnectionSettings

DEFAULT_STORE_NAME = "mdm_connection.json"

def _store_path() -> Path:
    env = os.environ.get("MDM_CONN_FILE")
    if env:
        return Path(env)
    try:
        from shared.services.workspace import connection_file_path
        return connection_file_path()
    except Exception:
        return Path.cwd() / DEFAULT_STORE_NAME

def load_saved_connection() -> Optional[ConnectionSettings]:
    path = _store_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        server = (data.get("server") or "").strip()
        if not server:
            return None
        return ConnectionSettings(
            server=server,
            username=data.get("username") or "",
            password=data.get("password") or "",
            database=data.get("database") or "",
            auth_mode=data.get("auth_mode") or "sql",
            trust_server_certificate=bool(data.get("trust_server_certificate", True)),
            encrypt=bool(data.get("encrypt", False)),
            driver=data.get("driver"),
        )
    except Exception:
        return None

def save_connection(settings: ConnectionSettings) -> Path:
    path = _store_path()
    payload = {
        "server": settings.server,
        "username": settings.username,
        "password": settings.password,
        "database": settings.database,
        "auth_mode": settings.auth_mode,
        "trust_server_certificate": settings.trust_server_certificate,
        "encrypt": settings.encrypt,
        "driver": settings.driver,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path

def clear_saved_connection() -> None:
    path = _store_path()
    if path.is_file():
        path.unlink()
