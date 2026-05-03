import io
import re
import json
import yaml
import random
import mistune
import resvg_py
from html import escape
from pathlib import Path
from bs4 import BeautifulSoup
from markitdown import MarkItDown
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, Response
from fastapi.templating import Jinja2Templates
from fastapi.responses import PlainTextResponse, JSONResponse, FileResponse, RedirectResponse
from jinja2.exceptions import TemplateNotFound
from .error import error_page
from .database import AccessCounter
from .middleware import Middleware, server_version, onion_hostname

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(Middleware)
templates = Jinja2Templates(directory=Path.cwd().joinpath("public"))
markitdown = MarkItDown()
accesscounter = AccessCounter()
templates.env.globals["get_access_count"] = accesscounter.get
templates.env.globals["server_version"] = server_version
templates.env.globals["onion_site_url"] = f"http://{onion_hostname}/"
templates.env.filters["re_sub"] = lambda s, pattern, repl: re.sub(pattern, repl, s)

class CustomHTMLRenderer(mistune.HTMLRenderer):
    def block_code(self, code, **attrs):
        return f'<pre>{mistune.escape(code)}</pre>\n'
htmlitdown = mistune.create_markdown(renderer=CustomHTMLRenderer(escape=False))

@property
def this_year() -> int:
    return datetime.now(ZoneInfo("Asia/Tokyo")).year
templates.env.globals["this_year"] = this_year

@property
def this_year_in_heisei() -> int: # heysay is not ended.
    return datetime.now(ZoneInfo("Asia/Tokyo")).year - 1989
templates.env.globals["this_year_in_heisei"] = this_year_in_heisei

def get_daily_quote() -> str:
    seed = str(datetime.now(timezone.utc).date())
    with Path.cwd().joinpath("public", "quotes.txt").open("r") as f:
        quotes = f.read().strip().split("\n")
    return random.Random(seed).choice(quotes)
templates.env.globals["get_daily_quote"] = get_daily_quote

def resolve_static_file(full_path: str) -> Path | None:
    base_dir = Path.cwd().joinpath("public")
    target_path = (base_dir / full_path.lstrip('/')).resolve()
    if not target_path.is_relative_to(base_dir.resolve()):
        raise PermissionError()
    return target_path if target_path.is_file() else None

def resolve_shorturl(shorturls: dict, full_path: str) -> str | None:
    current_id = full_path.strip().rstrip("/")
    visited = set()
    for _ in range(10):
        if current_id in visited or current_id not in shorturls:
            return None
        visited.add(current_id)
        entry = shorturls[current_id]
        if entry["type"] in ["redirect", "alias"]:
            if entry["type"] == "redirect":
                return entry["content"]
            current_id = entry["content"]
    return None

@app.api_route("/ping", methods=["GET"])
async def ping(request: Request):
    return PlainTextResponse("pong!", status_code=200)

@app.api_route("/echo", methods=["GET"])
async def echo(request: Request):
    return JSONResponse(request.scope["log"], status_code=200)

@app.api_route("/status", methods=["GET"])
async def status(request: Request):
    return JSONResponse(
        {
            "status": "ok",
            "version": server_version,
            "daily_quote": get_daily_quote(),
            "access_count": accesscounter.get()
        },
        status_code=200
    )

@app.api_route("/welcome", methods=["GET"])
async def welcome(request: Request):
    return PlainTextResponse(
        f"""
■   ■ ■■■■■ ■■■■   ■■■■  ■■■  ■   ■ ■■■■■
■■  ■ ■     ■   ■ ■     ■   ■ ■■  ■ ■
■■  ■ ■     ■   ■ ■     ■   ■ ■■  ■ ■
■ ■ ■ ■■■■  ■■■■  ■     ■   ■ ■ ■ ■ ■■■■
■  ■■ ■     ■ ■   ■     ■   ■ ■  ■■ ■
■  ■■ ■     ■  ■  ■     ■   ■ ■  ■■ ■
■   ■ ■■■■■ ■   ■  ■■■■  ■■■  ■   ■ ■■■■■

nercone.dev ({server_version})
welcome to nercone.dev!
        """.strip() + "\n",
        status_code=200
    )

@app.api_route("/error/{code}", methods=["GET", "POST", "HEAD"])
async def fake_error_page(request: Request, code: str):
    return error_page(templates=templates, request=request, status_code=int(code))

@app.api_route("/assets/images/thumbnails/{path:path}", methods=["GET"])
async def thumbnail(request: Request, path: str) -> Response:
    title = request.query_params.get("title", "Untitled Page")
    description = request.query_params.get("description", "No description.")
    template_type = request.query_params.get("template", "normal")

    parts = [p for p in path.strip("/").split("/") if p]
    path_display = "nercone.dev / " + " / ".join(parts) if parts else "nercone.dev"

    svg_filename = "error.svg" if template_type == "error" else "normal.svg"
    fonts_dir = Path.cwd().joinpath("public", "assets", "fonts")

    svg_path = Path.cwd().joinpath("public", "assets", "images", "thumbnails", svg_filename)
    svg = svg_path.read_text(encoding="utf-8")
    svg = svg.replace("__PATH__", escape(path_display))
    svg = svg.replace("__TITLE__", escape(title))
    svg = svg.replace("__DESCRIPTION__", escape(description))

    font_files = [
        str(fonts_dir / "MesloBIZUD-Regular.ttf"),
        str(fonts_dir / "InterBIZUD-Regular.ttf"),
        str(fonts_dir / "InterBIZUD-Bold.ttf"),
    ]
    png = resvg_py.svg_to_bytes(svg, font_files=font_files, width=1200, height=630)
    return Response(content=png, media_type="image/png")

@app.api_route("/{full_path:path}", methods=["GET", "POST", "HEAD"])
async def default_response(request: Request, full_path: str) -> Response:
    if not full_path.endswith(".html") and not full_path.endswith(".md"):
        try:
            if static := resolve_static_file(full_path):
                return FileResponse(static)
        except PermissionError:
            return error_page(templates, request, 403, "何をしてるんです？脆弱性報告のためならいいのですが、データ盗んで悪用するためなら今すぐにやめてくださいね？", "ディレクトリトラバーサルね、知ってる。公開してないところ覗きたいの？えっt")

    markdown_mode = False
    markdown_ua = ["curl", "claude-user", "chatgpt-user", "google-extended", "perplexity-user"]

    if "text/markdown" in request.headers.get("accept", "").lower():
        markdown_mode = True
    elif any([ua in request.headers.get("user-agent", "").lower() for ua in markdown_ua]):
        markdown_mode = True
    elif full_path.endswith(".md"):
        markdown_mode = True

    if full_path in ["", "/"]:
        template_candidates = ["index.html", "README.html"]
        markdown_candidates = ["index.md", "README.md"]
    elif full_path.endswith(".html"):
        template_candidates = [f"{full_path[:-5].strip('/')}.html"]
        markdown_candidates = [f"{full_path[:-5].strip('/')}.md"]
    elif full_path.endswith(".md"):
        template_candidates = [f"{full_path[:-3].strip('/')}.html"]
        markdown_candidates = [f"{full_path[:-3].strip('/')}.md"]
    else:
        template_candidates = [f"{full_path.strip('/')}.html", f"{full_path.strip('/')}/index.html", f"{full_path.strip('/')}/README.html"]
        markdown_candidates = [f"{full_path.strip('/')}.md",   f"{full_path.strip('/')}/index.md",   f"{full_path.strip('/')}/README.md"]

    def try_templates():
        for name in template_candidates:
            try:
                if markdown_mode:
                    content = templates.env.get_template(name).render(request=request)
                    soup = BeautifulSoup(content, "html.parser")
                    main = str(soup.find("main")) if soup.find("main") else content
                    markdown = markitdown.convert_stream(io.BytesIO(main.encode("utf-8")), file_extension=".html")
                    return PlainTextResponse(markdown.text_content, status_code=200, media_type="text/markdown")
                else:
                    return templates.TemplateResponse(status_code=200, request=request, name=name)
            except TemplateNotFound:
                continue
        return None

    def try_markdowns():
        for name in markdown_candidates:
            try:
                if not (markdown_path := resolve_static_file(name)):
                    continue
                with markdown_path.open("r") as f:
                    markdown = f.read()
                if markdown_mode:
                    return PlainTextResponse(markdown, status_code=200, media_type="text/markdown")
                else:
                    if not markdown.startswith("---"):
                        front = {}
                        body = markdown
                    else:
                        end = markdown.find("\n---", 3)
                        if end == -1:
                            front = {}
                            body = markdown
                        else:
                            front = yaml.safe_load(markdown[3:end]) or {}
                            body = markdown[end+4:].lstrip("\n")

                    html = htmlitdown(body)
                    source = f"{{% extends \"/base.html\" %}}\n"
                    for block in front:
                        source += f"{{% block {block} %}}{front[block]}{{% endblock %}}\n"
                    source += f"{{% block content %}}\n{html}\n{{% endblock %}}\n"

                    content = templates.env.from_string(source).render(request=request)
                    return Response(content=content, status_code=200, media_type="text/html")
            except PermissionError:
                return error_page(templates, request, 403, "何をしてるんです？脆弱性報告のためならいいのですが、データ盗んで悪用するためなら今すぐにやめてくださいね？", "ディレクトリトラバーサルね、知ってる。公開してないところ覗きたいの？えっt")
        return None

    for try_fn in ([try_markdowns, try_templates] if markdown_mode else [try_templates, try_markdowns]):
        if response := try_fn():
            accesscounter.increase()
            return response

    try:
        path = Path.cwd().joinpath("public", "shorturls.json")
        if not path.exists():
            return error_page(templates, request, 500, "短縮URLの処理のためのJSONファイルがありません。", "設定ファイルぐらい用意しておけよ！")
        shorturls = json.load(path.open("r", encoding="utf-8"))
    except Exception:
        return error_page(templates, request, 500, "短縮URLの処理のためのJSONファイルを正常に読み込めませんでした。", "なにこの設定ファイル読めないじゃない！")

    if result := resolve_shorturl(shorturls, full_path):
        return RedirectResponse(url=result)

    return error_page(templates, request, 404, "リクエストしたページは現在ご利用になれません。削除/移動されたか、URLが間違っている可能性があります。", "そんなページ知らないっ！")
