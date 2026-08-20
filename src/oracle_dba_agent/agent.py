"""The DBA agent: drives the MCP toolset and produces a diagnosis report.

The agent never touches the database directly — it always goes through MCP tool
calls, either against the in-process FastMCP server or a remote one over HTTP
(set DBA_MCP_URL).
"""

from __future__ import annotations

import json
from typing import Any

from fastmcp import Client

from .config import get_settings
from .mcp_server import mcp


def _client() -> Client:
    url = get_settings().mcp_server_url
    return Client(url) if url else Client(mcp)


def _payload(result: Any) -> Any:
    """Extract structured data from a FastMCP CallToolResult."""
    data = getattr(result, "data", None)
    if data is not None:
        return data
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured.get("result", structured)
    content = getattr(result, "content", None) or []
    if content and getattr(content[0], "text", None):
        try:
            return json.loads(content[0].text)
        except json.JSONDecodeError:
            return content[0].text
    return result


async def list_tools() -> list[dict[str, str]]:
    async with _client() as client:
        return [{"name": t.name, "description": t.description or ""} for t in await client.list_tools()]


async def call_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
    async with _client() as client:
        return _payload(await client.call_tool(name, arguments or {}))


async def diagnose() -> dict[str, Any]:
    """Run a full real-time diagnosis through the MCP server."""
    async with _client() as client:
        return _payload(await client.call_tool("oracle_diagnose", {}))


async def ping() -> dict[str, Any]:
    async with _client() as client:
        return _payload(await client.call_tool("oracle_ping", {}))
