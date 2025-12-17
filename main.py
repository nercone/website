import re
import json
import uuid
import shutil
import random
import subprocess
import uvicorn
import multiprocessing
from enum import Enum
from pathlib import Path
from itertools import permutations
from functools import lru_cache
from datetime import datetime, timezone
from nercone_modern.color import ModernColor
from nercone_modern.logging import ModernLogging
from fastapi import FastAPI, Request, Response
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, JSONResponse
from jinja2.exceptions import TemplateNotFound
from bs4 import BeautifulSoup

app = FastAPI()
templates = Jinja2Templates(directory="html")
log_filepath = Path(__file__).parent.joinpath("logs", "main.log")
logger = ModernLogging("nercone-webserver", filepath=str(log_filepath))
log_exclude_paths = ["status"]
block_messages = [
    "Nice try, but this system is a bit ahead of that.",
    "That approach is already accounted for.",
    "That attack is far too primitive for this system.",
    "Please upgrade your attack techniques.",
    "You'll need a better idea than that.",
    "That attack is far too low-level to matter.",
    "Outdated exploit. Try again.",
    "404 Vulnerability Not Found.",
    "Please upgrade your hacking skills."
]
MAX_BODY_LOG_SIZE = 1024 * 100 # 100KB

def strip_ip_chars(s: str) -> str:
    return re.sub(r'[^0-9A-Fa-f:.]', '', s)

@lru_cache(maxsize=128)
def whois(address: str) -> str | None:
    whois_proc = subprocess.Popen([shutil.which("whois"), strip_ip_chars(str(address))], encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.STDOUT) # type: ignore
    whois_output = ""
    for line in whois_proc.stdout: # type: ignore
        if line.strip() != "":
            whois_output += line
    whois_proc.wait()
    if whois_proc.returncode == 0:
        return whois_output
    else:
        return None

def list_articles():
    base_dir = Path(__file__).parent / "html"
    article_dir = base_dir / "blog" / "article"
    articles = []
    if not article_dir.exists():
        return articles
    html_files = sorted(article_dir.glob("*.html"))
    for file_path in html_files:
        try:
            relative_path = file_path.relative_to(base_dir).as_posix()
            template = templates.env.get_template(relative_path)
            rendered_html = template.render()
            soup = BeautifulSoup(rendered_html, "html.parser")
            title_tag = soup.find("title")
            title = str(title_tag.string).replace(" - Nercone Blog", "") if title_tag else "No Title"
            meta_desc = soup.find("meta", attrs={"name": "description"})
            description = meta_desc["content"] if meta_desc else ""
            articles.append({
                "title": title,
                "description": description,
                "filename": file_path.name,
                "path": f"/blog/article/{file_path.name.replace('.html', '')}"
            })
        except Exception as e:
            print(f"Error parsing {file_path}: {e}")
            continue
    return articles
templates.env.globals["list_articles"] = list_articles

class AccessClientType(Enum):
    FastGet = "FastGet"
    cURL = "cURL"
    Wget = "Wget"
    Firefox = "Firefox"
    Chrome = "Chrome"
    Opera = "Opera"
    Edge = "Edge"
    Safari = "Safari"
    Unknown = "Unknown"

@app.middleware("http")
async def middleware(request: Request, call_next):
    try:
        start_time = datetime.now(timezone.utc)
        if request.scope.get("state", {}).get("is_retry", False):
            return await call_next(request)
        request_body = await request.body()
        async def receive():
            return {"type": "http.request", "body": request_body}
        original_scope = request.scope.copy() # type: ignore
        original_path = original_scope['path']
        host = request.headers.get("host", "").split(":")[0]
        host_parts = host.split('.')
        subdomains = []
        if len(host_parts) > 1 and host_parts[-1] == 'localhost':
            s_parts = host_parts[:-1]
            if s_parts:
                subdomains = s_parts
        elif len(host_parts) > 2:
            s_parts = host_parts[:-2]
            if s_parts and s_parts != ['www']:
                subdomains = s_parts
        response = None
        if subdomains:
            retry_strategies = ["/".join(reversed(subdomains))]
            for p in permutations(subdomains):
                path_candidate = "/".join(p)
                if path_candidate not in retry_strategies:
                    retry_strategies.append(path_candidate)
            for sub_prefix in retry_strategies:
                new_path = f"/{sub_prefix}{original_path if original_path != '/' else ''}"
                response_status = None
                response_headers = None
                response_body_chunks = []
                async def capture_send(message):
                    nonlocal response_status, response_headers
                    if message["type"] == "http.response.start":
                        response_status = message["status"]
                        response_headers = message["headers"]
                    elif message["type"] == "http.response.body":
                        response_body_chunks.append(message.get("body", b""))
                retry_scope = original_scope.copy()
                retry_scope['path'] = new_path
                if 'raw_path' in retry_scope:
                    retry_scope['raw_path'] = new_path.encode('utf-8')
                retry_scope['state'] = retry_scope.get('state', {})
                retry_scope['state']['is_retry'] = True
                try:
                    await app(retry_scope, receive, capture_send)
                except Exception:
                    continue
                if response_status is not None and response_status < 400:
                    final_body = b"".join(response_body_chunks)
                    decoded_headers = {
                        key.decode("latin-1"): value.decode("latin-1")
                        for key, value in response_headers
                    }
                    response = Response(
                        content=final_body,
                        status_code=response_status,
                        headers=decoded_headers
                    )
                    request.scope['path'] = new_path
                    break
            if response is None:
                request.scope['path'] = original_path
                response = await call_next(request)
        else:
            response = await call_next(request)
        access_id = str(uuid.uuid4()).lower()
        user_agent = request.headers.get("user-agent", "")
        request.state.client_type = AccessClientType.Unknown
        if "fastget" in user_agent.lower():
            request.state.client_type = AccessClientType.FastGet
        elif "curl" in user_agent.lower():
            request.state.client_type = AccessClientType.cURL
        elif "wget" in user_agent.lower():
            request.state.client_type = AccessClientType.Wget
        elif "firefox" in user_agent.lower():
            request.state.client_type = AccessClientType.Firefox
        elif "opr" in user_agent.lower():
            request.state.client_type = AccessClientType.Opera
        elif "edg" in user_agent.lower():
            request.state.client_type = AccessClientType.Edge
        elif "chrome" in user_agent.lower():
            request.state.client_type = AccessClientType.Chrome
        elif "safari" in user_agent.lower():
            request.state.client_type = AccessClientType.Safari
        request.state.client_type = request.state.client_type.value
        proxy_route = []
        origin_client_host = request.client.host # type: ignore
        if "X-Forwarded-For" in request.headers:
            proxy_route = request.headers.get("X-Forwarded-For", "").split(",")
            origin_client_host = proxy_route[0]
        exception: Exception | None = None
        response.headers["Server"] = "Nercone Web Server"
        response_body = b""
        if not isinstance(response, (FileResponse, RedirectResponse)):
            if hasattr(response, "body_iterator"):
                async for chunk in response.body_iterator:
                    response_body += chunk
                response = Response(
                    content=response_body,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type,
                )
            elif hasattr(response, "body"):
                response_body = response.body
        end_time = datetime.now(timezone.utc)
        access_log_dir = Path(__file__).parent.joinpath("logs", "access")
        if not access_log_dir.exists():
            access_log_dir.mkdir(parents=True, exist_ok=True)
        with access_log_dir.joinpath(f"{access_id}.txt").open("w", encoding="utf-8") as f:
            f.write("[REQUEST]\n")
            f.write(f"REQUEST.TIME: {start_time.strftime('%Y-%m-%dT%H:%M:%SZ')}\n")
            f.write(f"REQUEST.METH: {request.method}\n")
            f.write(f"REQUEST.HOST: {request.client.host}\n") # type: ignore
            f.write(f"REQUEST.PORT: {request.client.port}\n") # type: ignore
            f.write(f"REQUEST.ORGN: {origin_client_host}\n")
            f.write(f"REQUEST.TYPE: {request.state.client_type}\n")
            f.write(f"REQUEST.URL : {request.url}\n")
            for i in range(len(proxy_route)):
                if proxy_route[i] == origin_client_host:
                    f.write(f"REQUEST.ROUT[{i}]: {proxy_route[i].strip()} (O)\n")
                elif proxy_route[i] == request.client.host: # type: ignore
                    f.write(f"REQUEST.ROUT[{i}]: {proxy_route[i].strip()} (P)\n")
                else:
                    f.write(f"REQUEST.ROUT[{i}]: {proxy_route[i].strip()} (M)\n")
            for key, value in request.headers.items():
                f.write(f"REQUEST.HEAD[{key}]: {value}\n")
            for key, value in request.cookies.items():
                f.write(f"REQUEST.COOK[{key}]: {value}\n")
            if 0 < len(request_body) <= MAX_BODY_LOG_SIZE:
                try:
                    decoded_body = request_body.decode("utf-8")
                    f.write("REQUEST.BODY: ---\n")
                    f.write(decoded_body)
                    f.write("---\n")
                except UnicodeDecodeError:
                    pass
            f.write("\n")
            try:
                whois_result = whois(origin_client_host)
                if whois_result is not None:
                    f.write("[WHOIS]\n")
                    f.write(whois_result)
                    f.write("\n")
            except:
                pass
            f.write("[RESPONSE]\n")
            f.write(f"RESPONSE.TIME: {end_time.strftime('%Y-%m-%dT%H:%M:%SZ')}\n")
            f.write(f"RESPONSE.CODE: {response.status_code}\n")
            f.write(f"RESPONSE.CHAR: {response.charset}\n")
            for key, value in response.headers.items():
                f.write(f"RESPONSE.HEAD[{key}]: {value}\n")
            if 0 < len(response_body) <= MAX_BODY_LOG_SIZE:
                try:
                    charset = response.charset if response.charset else 'utf-8'
                    try:
                        decoded_body = response_body.decode(charset)
                    except (UnicodeDecodeError, LookupError):
                        decoded_body = response_body.decode('utf-8', errors='replace')
                    f.write("RESPONSE.BODY: ---\n")
                    f.write(decoded_body)
                    f.write("---\n")
                except UnicodeDecodeError:
                    pass
            if exception:
                f.write("\n")
                f.write("[EXCEPTION]\n")
                f.write(str(exception))
        log_level = "INFO"
        status_code_color = "magenta"
        if str(response.status_code).startswith("1"):
            log_level = "INFO"
            status_code_color = "cyan"
        elif str(response.status_code).startswith("2"):
            log_level = "INFO"
            status_code_color = "green"
        elif str(response.status_code).startswith("3"):
            log_level = "INFO"
            status_code_color = "blue"
        elif str(response.status_code).startswith("4"):
            log_level = "WARNING"
            status_code_color = "yellow"
        elif str(response.status_code).startswith("5"):
            log_level = "ERROR"
            status_code_color = "red"
        if not request.scope['path'].strip("/") in log_exclude_paths:
            logger.log(f"{ModernColor.color(status_code_color)}{response.status_code}{ModernColor.color('reset')} {access_id} {request.client.host} {ModernColor.color('gray')}{request.url}{ModernColor.color('reset')}", level_text=log_level) # type: ignore
        return response
    except:
        logger.log("Fatal exception in middleware!!!", level_text="ERROR")
        raise

@app.api_route("/status", methods=["GET"])
async def short_url(request: Request):
    return JSONResponse({"status": "ok"}, status_code=200)

@app.api_route("/to/{url_id:path}", methods=["GET", "POST", "HEAD"])
async def short_url(request: Request, url_id: str):
    json_path = Path(__file__).parent / "shorturls.json"
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
    if any(t in full_path for t in ["php", "cgi", "wp-", "admin", "plugins"]):
        return PlainTextResponse(random.choice(block_messages), status_code=404)
    if not full_path.endswith(".html"):
        base_dir = Path(__file__).parent / "files"
        safe_full_path = full_path.lstrip('/')
        target_path = (base_dir / safe_full_path).resolve()
        if not str(target_path).startswith(str(base_dir.resolve())):
            return PlainTextResponse(random.choice(block_messages), status_code=403)
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
            return templates.TemplateResponse(
                status_code=200,
                request=request,
                name=template_name
            )
        except TemplateNotFound:
            continue
    return templates.TemplateResponse(
        status_code=404,
        request=request,
        name="404.html"
    )

if __name__ == "__main__":
    logger.log("Nercone Web Server Started.")
    cores_count = multiprocessing.cpu_count()
    logger.log(f"CPU Core Count: {cores_count} Core(s)")
    workers_count = cores_count*2
    logger.log(f"Starting with {workers_count} workers.")
    uvicorn.run("__main__:app", host="0.0.0.0", port=80, log_level="error", workers=workers_count)
    logger.log("Nercone Web Server Stopped.")
