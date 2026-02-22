from fastapi import Response
from fastapi.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

onion_hostname = "4sbb7xhdn4meuesnqvcreewk6sjnvchrsx4lpnxmnjhz2soat74finid.onion"
hostnames = ["localhost", "nercone.dev", "d-g-c.net", "diamondgotcat.net", onion_hostname]

class Middleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        hostname = headers.get(b"host", b"").decode().split(":")[0].strip()

        if not any([hostname.endswith(candidate) for candidate in hostnames]):
            response = PlainTextResponse("許可されていないホスト名でのアクセスです。", status_code=400)
            await self._send_with_headers(response, scope, receive, send)
            return

        hostname_parts = hostname.split(".")
        if hostname_parts[1:] == ["localhost"]:
            subdomain = ".".join(hostname_parts[:-1])
        else:
            subdomain = ".".join(hostname_parts[:-2])

        body = await self._read_body(receive)
        async def cached_receive():
            return {"type": "http.request", "body": body, "more_body": False}

        if subdomain not in ["", "www"]:
            original_path = scope["path"] if scope["path"].strip() else "/"
            subdomain_path = f"/{subdomain}{original_path}"

            response = await self._get_response(scope, cached_receive, subdomain_path)
            if response.status_code < 400:
                await self._send_with_headers(response, scope, cached_receive, send)
                return

            response = await self._get_response(scope, cached_receive, original_path)
            await self._send_with_headers(response, scope, cached_receive, send)
        else:
            response = await self._get_response(scope, cached_receive, scope["path"])
            await self._send_with_headers(response, scope, cached_receive, send)

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

    async def _send_with_headers(self, response: Response, scope, receive, send):
        response.headers["Server"] = "nercone"
        response.headers["Onion-Location"] = f"http://{onion_hostname}/"
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "*"
        await response(scope, receive, send)
