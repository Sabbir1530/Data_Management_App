"""Thin wrappers around the two stored procedures."""
from __future__ import annotations

from sqlalchemy import text


def _id(v: str):
    return int(v) if v.isdigit() else v


def run_remap(engine, old: str, new: str, usr: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "SET NOCOUNT ON; EXEC SNDBX_PDIDW.MDM.spRemap_NAD_EU_XREF "
                "@OLD_ID=:o, @OLD_ID_TYP='EU', @NEW_ENDUSER_ID=:n, @USR_ID=:u"
            ),
            {"o": _id(old), "n": _id(new), "u": _id(usr)},
        )
