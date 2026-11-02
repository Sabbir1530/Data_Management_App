import pyodbc
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from shared.models.connection import ConnectionSettings

def get_available_odbc_driver():
    driver_names = [
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 13 for SQL Server",
        "SQL Server",
    ]
    available_drivers = pyodbc.drivers()
    for driver in driver_names:
        if driver in available_drivers:
            return driver
    raise RuntimeError(f"No ODBC SQL Server driver found. Available: {available_drivers}")

def build_connection_url(connection_settings, database_name):
    selected_database = database_name or connection_settings.database or "master"
    driver = connection_settings.driver or get_available_odbc_driver()
    query = {"driver": driver}

    if connection_settings.auth_mode == "windows":
        query["trusted_connection"] = "yes"
    else:
        query["TrustServerCertificate"] = "yes" if connection_settings.trust_server_certificate else "no"
        query["Encrypt"] = "yes" if connection_settings.encrypt else "no"

    return URL.create(
        "mssql+pyodbc",
        username=connection_settings.username or None,
        password=connection_settings.password or None,
        host=connection_settings.server,
        database=selected_database,
        query=query,
    )

def create_database_engine(connection_settings, database_name=None):
    return create_engine(
        build_connection_url(connection_settings, database_name),
        pool_pre_ping=True,
    )

def list_accessible_databases(connection_settings):
    """List user databases via SQLAlchemy only (no pandas — avoids version quirks)."""
    engine = create_database_engine(connection_settings, "master")
    db_query = text(
        """
        SELECT name FROM sys.databases
        WHERE name NOT IN ('master', 'tempdb', 'model', 'msdb')
        AND HAS_DBACCESS(name) = 1
        ORDER BY name;
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(db_query).fetchall()
    return [r[0] for r in rows]
