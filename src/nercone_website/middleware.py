import rjsmin
import rcssmin
import subprocess
from scour import scour
from fastapi import Response
from fastapi.responses import PlainTextResponse
from starlette.types import Scope, ASGIApp, Receive, Send
from .logger import log_access, finalize_log

server_version = subprocess.run(["/usr/bin/git", "rev-parse", "--short", "HEAD"], text=True, capture_output=True).stdout.strip()
onion_hostname = "4sbb7xhdn4meuesnqvcreewk6sjnvchrsx4lpnxmnjhz2soat74finid.onion"
hostnames = ["localhost", "nercone.dev", "nerc1.dev", "diamondgotcat.net", "d-g-c.net", onion_hostname]

class Middleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        hostname = headers.get(b"host", b"").decode().split(":")[0].strip()

        hostname_parts = hostname.split(".")
        if hostname_parts[1:] == ["localhost"]:
            subdomain = ".".join(hostname_parts[:-1])
        else:
            subdomain = ".".join(hostname_parts[:-2])

        if scope["type"] == "websocket":
            if subdomain not in ["", "www"]:
                original_path = scope["path"] if scope["path"].strip() else "/"
                subdomain_path = f"/{'/'.join(subdomain.split('.')[::-1])}{original_path}"
                scope = dict(scope, path=subdomain_path)
            await self.app(scope, receive, send)
            return

        scope["log"] = log_access(scope)

        if not any([hostname.endswith(candidate) for candidate in hostnames]):
            response = PlainTextResponse("許可されていないホスト名でのアクセスです。", status_code=400)
            await self._send(response, scope, receive, send)
            finalize_log(scope["log"], response.status_code)
            return

        body = await self._read_body(receive)
        async def cached_receive():
            return {"type": "http.request", "body": body, "more_body": False}

        if subdomain not in ["", "www"]:
            original_path = scope["path"] if scope["path"].strip() else "/"
            subdomain_path = f"/{'/'.join(subdomain.split('.')[::-1])}{original_path}"

            response = await self._get_response(scope, cached_receive, subdomain_path)
            if response.status_code < 400:
                await self._send(response, scope, cached_receive, send)
                finalize_log(scope["log"], response.status_code)
                return

            response = await self._get_response(scope, cached_receive, original_path)
            await self._send(response, scope, cached_receive, send)
            finalize_log(scope["log"], response.status_code)
        else:
            response = await self._get_response(scope, cached_receive, scope["path"])
            await self._send(response, scope, cached_receive, send)
            finalize_log(scope["log"], response.status_code)

    async def _get_response(self, scope: Scope, receive: Receive, path: str) -> Response:
        new_scope = dict(scope, path=path)

        status_code = 200
        resp_headers = []
        body_parts = []

        async def capture_send(message):
            nonlocal status_code, resp_headers
            if message["type"] == "http.response.start":
                status_code = message["status"]
                resp_headers = message.get("headers", [])
            elif message["type"] == "http.response.body":
                body_parts.append(message.get("body", b""))

        body = await self._read_body(receive)
        async def cached_receive():
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(new_scope, cached_receive, capture_send)

        response = Response(
            content=b"".join(body_parts),
            status_code=status_code,
        )
        if response.status_code == 404 and path != "/" and path.endswith("/"):
            return await self._get_response(scope, cached_receive, path.rstrip("/"))
        for k, v in resp_headers:
            response.headers.raw.append((k, v))
        return response

    async def _read_body(self, receive: Receive) -> bytes:
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        return body

    async def _send(self, response: Response, scope, receive, send):
        content_type = response.headers.get("content-type", "")

        response.headers["Server"] = f"nercone.dev ({server_version})"
        response.headers["Onion-Location"] = f"http://{onion_hostname}/"
        response.headers["Link"] = "<https://nercone.dev/sitemap.xml>; rel=\"sitemap\", <https://nercone.dev/robots.txt>; rel=\"robots\""

        if "access-control-allow-origin" not in response.headers:
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "*"
            response.headers["Access-Control-Allow-Headers"] = "*"

        if "referrer-policy" not in response.headers:
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        if "content-security-policy" not in response.headers:
            response.headers["Content-Security-Policy"] = "default-src 'self' assets.nercone.dev; script-src 'self' assets.nercone.dev 'unsafe-inline'; style-src 'self' assets.nercone.dev fonts.googleapis.com 'unsafe-inline'; font-src 'self' assets.nercone.dev fonts.gstatic.com; img-src 'self' assets.nercone.dev t3tra.dev drsb.f5.si data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'; upgrade-insecure-requests;"

        if "permissions-policy" not in response.headers:
            response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), accelerometer=(), gyroscope=(), magnetometer=(), display-capture=()"

        if any(content_type.startswith(t) for t in ["text/html", "text/css", "text/javascript", "application/javascript"]):
            response.headers["Cache-Control"] = "no-cache"
        else:
            response.headers["Cache-Control"] = "public, max-age=3600"

        if "text/css" in content_type:
            try:
                response.body = rcssmin.cssmin(response.body.decode("utf-8", errors="replace")).encode("utf-8")
            except Exception:
                pass
        elif any(content_type.startswith(t) for t in ["text/javascript", "application/javascript"]):
            try:
                response.body = rjsmin.jsmin(response.body.decode("utf-8", errors="replace")).encode("utf-8")
            except Exception:
                pass
        elif "image/svg+xml" in content_type:
            try:
                options = scour.generateDefaultOptions()
                options.newlines = False
                options.shorten_ids = True
                options.strip_comments = True
                response.body = scour.scourString(response.body.decode("utf-8", errors="replace"), options).encode("utf-8")
            except Exception:
                pass
        response.headers["Content-Length"] = str(len(response.body))

        await response(scope, receive, send)
