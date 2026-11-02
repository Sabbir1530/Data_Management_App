from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ConnectionSettings:
    server: str
    username: str = ""
    password: str = ""
    database: str = ""
    auth_mode: str = "sql"
    trust_server_certificate: bool = True
    encrypt: bool = False
    driver: Optional[str] = None
