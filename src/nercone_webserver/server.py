import json
import random
import psutil
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime, timezone
from fastapi import FastAPI, Request, Response
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, JSONResponse
from jinja2.exceptions import TemplateNotFound
from .database import AccessCounter
from .middleware import Middleware, onion_hostname

app = FastAPI()
app.add_middleware(Middleware)
templates = Jinja2Templates(directory=Path.cwd().joinpath("public"))
accesscounter = AccessCounter()
templates.env.globals["server_version"] = subprocess.run(["/usr/bin/git", "rev-parse", "--short", "HEAD"], text=True, capture_output=True).stdout.strip()
templates.env.globals["onion_site_url"] = f"http://{onion_hostname}/"
templates.env.globals["get_access_count"] = accesscounter.get

def get_current_year():
    return str(datetime.now(ZoneInfo("Asia/Tokyo")).year)
templates.env.globals["get_current_year"] = get_current_year

def get_daily_quote() -> str:
    seed = str(datetime.now(timezone.utc).date())
    with Path.cwd().joinpath("public", "quotes.txt").open("r") as f:
        quotes = f.read().strip().split("\n")
    return random.Random(seed).choice(quotes)
templates.env.globals["get_daily_quote"] = get_daily_quote

@app.api_route("/api/v1/status", methods=["GET"])
async def v1_status(request: Request):
    virtual_memory = psutil.virtual_memory()
    swap_memory = psutil.swap_memory()
    disk_usage = psutil.disk_usage("/")
    return JSONResponse(
        {
            "status": "ok",
            "access": accesscounter.get(),
            "phrase": get_daily_quote(),
            "resouces": {
                "cpu": {
                    "count": psutil.cpu_count(),
                    "percent": psutil.cpu_percent(interval=1)
                },
                "memory": {
                    "virtual": {
                        "total": virtual_memory.total,
                        "used": virtual_memory.used,
                        "available": virtual_memory.available,
                        "percent": virtual_memory.percent
                    },
                    "swap": {
                        "total": swap_memory.total,
                        "used": swap_memory.used,
                        "available": swap_memory.total - swap_memory.used,
                        "percent": swap_memory.percent
                    }
                },
                "storage": {
                    "total": disk_usage.total,
                    "used": disk_usage.used,
                    "available": disk_usage.free
                }
            }
        },
        status_code=200
    )

@app.api_route("/to/{url_id:path}", methods=["GET", "POST", "HEAD"])
async def short_url(request: Request, url_id: str):
    json_path = Path.cwd().joinpath("public", "shorturls.json")
    if not json_path.exists():
        return PlainTextResponse("Short URL configuration file not found.", status_code=500)
    try:
        with json_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return PlainTextResponse("Failed to load Short URL configuration.", status_code=500)
    current_id = url_id.strip().rstrip("/")
    visited = set()
    for _ in range(10):
        if current_id in visited:
            return PlainTextResponse("Circular alias detected.", status_code=500)
        visited.add(current_id)
        if current_id not in data:
            break
        entry = data[current_id]
        entry_type = entry.get("type")
        content = entry.get("content")
        if entry_type == "redirect":
            return RedirectResponse(url=content)
        elif entry_type == "alias":
            current_id = content
        else:
            break
    return templates.TemplateResponse(
        status_code=404,
        request=request,
        name="to/404.html"
    )

@app.api_route("/{full_path:path}", methods=["GET", "POST", "HEAD"])
async def default_response(request: Request, full_path: str) -> Response:
    if not full_path.endswith(".html"):
        base_dir = Path.cwd().joinpath("public")
        safe_full_path = full_path.lstrip('/')
        target_path = (base_dir / safe_full_path).resolve()
        if not str(target_path).startswith(str(base_dir.resolve())):
            return PlainTextResponse("ディレクトリトラバーサルね、知ってる", status_code=403)
        if target_path.exists() and target_path.is_file():
            return FileResponse(target_path)
    templates_to_try = []
    if full_path == "" or full_path == "/":
        templates_to_try.append("index.html")
    elif full_path.endswith(".html"):
        templates_to_try.append(full_path.lstrip('/'))
    else:
        clean_path = full_path.strip('/')
        templates_to_try.append(f"{clean_path}.html")
        templates_to_try.append(f"{clean_path}/index.html")
    for template_name in templates_to_try:
        try:
            response = templates.TemplateResponse(status_code=200, request=request, name=template_name)
            accesscounter.increase()
            return response
        except TemplateNotFound:
            continue
    return templates.TemplateResponse(status_code=404, request=request, name="404.html")
