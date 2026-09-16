"""Graphical web front end for every interceptorctl_menu.py operation."""

from __future__ import annotations

import asyncio
import socket
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from commands import STATUS_COMMANDS
from runner import CliRunner
from settings import BASE_DIR, Settings


STATIC_DIR = BASE_DIR / "static"
settings = Settings.from_env()
runner = CliRunner(
    settings.cli_path,
    str(settings.socket_path),
    settings.command_timeout,
)

app = FastAPI(
    title="Interceptorctl 图形化菜单",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def disable_ui_cache(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class CommandBody(BaseModel):
    args: List[str] = Field(min_length=1, max_length=40)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/info")
async def info() -> Dict[str, Any]:
    return {
        "ok": True,
        "hostname": socket.gethostname(),
        "cli_path": str(settings.cli_path),
        "cli_present": settings.cli_path.is_file(),
        "socket_path": str(settings.socket_path),
        "socket_present": settings.socket_path.exists(),
        "status_commands": [
            {"id": key, "label": label, "args": list(args)}
            for key, label, args in STATUS_COMMANDS
        ],
    }


@app.get("/healthz")
async def healthz() -> Dict[str, bool]:
    return {"ok": True}


@app.post("/api/command")
async def run_command(body: CommandBody) -> Dict[str, Any]:
    return await asyncio.to_thread(runner.run, body.args)


@app.post("/api/status")
async def refresh_status() -> Dict[str, Any]:
    results = []
    for key, label, args in STATUS_COMMANDS:
        result = await asyncio.to_thread(runner.run, args)
        results.append({"id": key, "label": label, "result": result})
    return {"ok": True, "results": results}
