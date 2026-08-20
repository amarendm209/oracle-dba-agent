"""FastAPI dashboard serving real-time Oracle diagnosis and solutions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import agent
from ..config import get_settings
from ..db import DatabaseUnavailable

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="AI Oracle DBA Agent", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/target")
def target() -> dict[str, Any]:
    settings = get_settings()
    return {
        "dsn": settings.dsn,
        "user": settings.oracle_user or None,
        "credentials_present": settings.credentials_present(),
        "mcp_server": settings.mcp_server_url or "in-process",
    }


@app.get("/api/tools")
async def tools() -> list[dict[str, str]]:
    return await agent.list_tools()


@app.get("/api/diagnose")
async def diagnose() -> dict[str, Any]:
    try:
        return await agent.diagnose()
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_root_cause(exc)) from exc


@app.post("/api/tools/{tool_name}")
async def run_tool(tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        return {"tool": tool_name, "result": await agent.call_tool(tool_name, arguments or {})}
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=_root_cause(exc)) from exc


@app.get("/api/health")
async def health() -> dict[str, Any]:
    try:
        return {"database": await agent.ping()}
    except Exception as exc:
        return {"database": {"ok": False, "error": _root_cause(exc)}}


def _root_cause(exc: BaseException) -> str:
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, DatabaseUnavailable) or cause.__cause__ is None:
            break
        cause = cause.__cause__
    return str(cause or exc)


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.web_host, port=settings.web_port)


if __name__ == "__main__":
    main()
