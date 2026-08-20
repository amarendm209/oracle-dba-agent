"""Read-only Oracle access layer."""

from __future__ import annotations

import datetime as dt
import decimal
import threading
from typing import Any

import oracledb

from .config import Settings, get_settings

_pool: oracledb.ConnectionPool | None = None
_pool_lock = threading.Lock()

_FORBIDDEN_PREFIXES = (
    "insert",
    "update",
    "delete",
    "merge",
    "drop",
    "create",
    "alter",
    "truncate",
    "grant",
    "revoke",
    "begin",
    "declare",
    "call",
)


class DatabaseUnavailable(RuntimeError):
    """Raised when the agent cannot reach the target database."""


def get_pool(settings: Settings | None = None) -> oracledb.ConnectionPool:
    global _pool
    settings = settings or get_settings()
    if not settings.credentials_present():
        raise DatabaseUnavailable(
            "Oracle credentials are missing. Set ORACLE_USER and ORACLE_PASSWORD "
            "(from your secret store) before starting the agent."
        )
    with _pool_lock:
        if _pool is None:
            try:
                _pool = oracledb.create_pool(
                    user=settings.oracle_user,
                    password=settings.oracle_password.get_secret_value(),
                    dsn=settings.dsn,
                    min=settings.pool_min,
                    max=settings.pool_max,
                    increment=1,
                )
            except oracledb.Error as exc:  # pragma: no cover - depends on live DB
                raise DatabaseUnavailable(f"Cannot connect to {settings.dsn}: {exc}") from exc
    return _pool


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close(force=True)
            _pool = None


def _normalize(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, oracledb.LOB):  # pragma: no cover - depends on live DB
        return value.read()
    return value


def query(sql: str, params: dict[str, Any] | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Run a read-only SELECT/WITH statement and return rows as dictionaries."""
    stripped = sql.strip().lstrip("(").lower()
    if stripped.startswith(_FORBIDDEN_PREFIXES):
        raise ValueError("Only read-only queries are allowed by the DBA agent")

    settings = get_settings()
    pool = get_pool(settings)
    try:
        with pool.acquire() as conn:
            conn.call_timeout = settings.query_timeout_seconds * 1000
            with conn.cursor() as cur:
                cur.arraysize = min(limit, 500)
                cur.execute(sql, params or {})
                columns = [d[0].lower() for d in cur.description]
                return [
                    dict(zip(columns, (_normalize(v) for v in row), strict=True))
                    for row in cur.fetchmany(limit)
                ]
    except oracledb.Error as exc:  # pragma: no cover - depends on live DB
        raise DatabaseUnavailable(str(exc)) from exc


def scalar(sql: str, params: dict[str, Any] | None = None) -> Any:
    rows = query(sql, params, limit=1)
    if not rows:
        return None
    return next(iter(rows[0].values()))
