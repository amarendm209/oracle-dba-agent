import pytest
from fastmcp import Client

from oracle_dba_agent.mcp_server import mcp

EXPECTED_TOOLS = {
    "oracle_ping",
    "oracle_overview",
    "oracle_active_sessions",
    "oracle_blocking_sessions",
    "oracle_top_wait_events",
    "oracle_current_waits",
    "oracle_top_sql",
    "oracle_sql_details",
    "oracle_tablespace_usage",
    "oracle_memory_health",
    "oracle_io_hotspots",
    "oracle_long_operations",
    "oracle_segment_health",
    "oracle_invalid_objects",
    "oracle_parameters",
    "oracle_run_query",
    "oracle_health_snapshot",
    "oracle_diagnose",
}


@pytest.mark.asyncio
async def test_server_exposes_dba_tools():
    async with Client(mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert EXPECTED_TOOLS <= names
