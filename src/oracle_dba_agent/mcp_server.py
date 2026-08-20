"""FastMCP server exposing the Oracle DBA toolset."""

from __future__ import annotations

import argparse
from typing import Any

from fastmcp import FastMCP

from . import dba_tools
from .diagnosis import diagnose

mcp: FastMCP = FastMCP(
    name="oracle-dba",
    instructions=(
        "Read-only Oracle performance diagnostics. Start with oracle_health_snapshot or "
        "oracle_diagnose for a full picture, then drill into oracle_top_sql / oracle_sql_details "
        "for statement-level tuning."
    ),
)


@mcp.tool(name="oracle_ping")
def oracle_ping() -> dict[str, Any]:
    """Verify connectivity to the target Oracle database."""
    return dba_tools.ping()


@mcp.tool(name="oracle_overview")
def oracle_overview() -> dict[str, Any]:
    """Instance identity, uptime, session counts and resource limits."""
    return dba_tools.database_overview()


@mcp.tool(name="oracle_active_sessions")
def oracle_active_sessions(limit: int = 25) -> list[dict[str, Any]]:
    """List currently active user sessions with wait event and SQL id."""
    return dba_tools.active_sessions(limit)


@mcp.tool(name="oracle_blocking_sessions")
def oracle_blocking_sessions(limit: int = 25) -> list[dict[str, Any]]:
    """List lock holders together with the sessions they are blocking."""
    return dba_tools.blocking_sessions(limit)


@mcp.tool(name="oracle_top_wait_events")
def oracle_top_wait_events(limit: int = 15) -> list[dict[str, Any]]:
    """Top non-idle wait events since instance startup."""
    return dba_tools.top_wait_events(limit)


@mcp.tool(name="oracle_current_waits")
def oracle_current_waits(limit: int = 20) -> list[dict[str, Any]]:
    """What sessions are waiting on right now, aggregated by event."""
    return dba_tools.current_waits(limit)


@mcp.tool(name="oracle_top_sql")
def oracle_top_sql(order_by: str = "elapsed", limit: int = 15) -> list[dict[str, Any]]:
    """Most expensive cached SQL by elapsed, cpu, gets, reads or executions."""
    return dba_tools.top_sql(order_by, limit)


@mcp.tool(name="oracle_sql_details")
def oracle_sql_details(sql_id: str) -> dict[str, Any]:
    """Full text, statistics and execution plan for a single SQL id."""
    return dba_tools.sql_details(sql_id)


@mcp.tool(name="oracle_tablespace_usage")
def oracle_tablespace_usage(limit: int = 30) -> list[dict[str, Any]]:
    """Space usage per tablespace, ordered by percentage used."""
    return dba_tools.tablespace_usage(limit)


@mcp.tool(name="oracle_memory_health")
def oracle_memory_health() -> dict[str, Any]:
    """Buffer/library cache efficiency, SGA pool sizes and PGA usage."""
    return dba_tools.memory_and_cache_health()


@mcp.tool(name="oracle_io_hotspots")
def oracle_io_hotspots(limit: int = 15) -> list[dict[str, Any]]:
    """Datafiles with the highest physical I/O and read latency."""
    return dba_tools.io_hotspots(limit)


@mcp.tool(name="oracle_long_operations")
def oracle_long_operations(limit: int = 15) -> list[dict[str, Any]]:
    """In-flight long operations reported by v$session_longops."""
    return dba_tools.long_running_operations(limit)


@mcp.tool(name="oracle_segment_health")
def oracle_segment_health(limit: int = 20) -> list[dict[str, Any]]:
    """Largest segments with optimizer statistics freshness."""
    return dba_tools.segment_health(limit)


@mcp.tool(name="oracle_invalid_objects")
def oracle_invalid_objects(limit: int = 25) -> list[dict[str, Any]]:
    """Invalid schema objects."""
    return dba_tools.invalid_objects(limit)


@mcp.tool(name="oracle_parameters")
def oracle_parameters(names: list[str] | None = None) -> list[dict[str, Any]]:
    """Values of performance-relevant initialization parameters."""
    return dba_tools.parameter_check(names)


@mcp.tool(name="oracle_run_query")
def oracle_run_query(sql: str, limit: int = 50) -> list[dict[str, Any]]:
    """Run an ad-hoc read-only query (SELECT/WITH only) against the database."""
    return dba_tools.run_readonly_query(sql, limit)


@mcp.tool(name="oracle_health_snapshot")
def oracle_health_snapshot() -> dict[str, Any]:
    """Collect every diagnostic area in a single call."""
    return dba_tools.health_snapshot()


@mcp.tool(name="oracle_diagnose")
def oracle_diagnose() -> dict[str, Any]:
    """Collect a snapshot and return prioritised findings with recommended solutions."""
    return diagnose(dba_tools.health_snapshot())


def main() -> None:
    parser = argparse.ArgumentParser(description="Oracle DBA FastMCP server")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "http", "sse"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
