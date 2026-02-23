from http import HTTPStatus
from fastapi import Request, Response
from fastapi.templating import Jinja2Templates

default_messages = {
    400: "日本語でおk",
    401: "見たいのならログインすることね",
    402: "夢が欲しけりゃ金払え！",
    403: "あんたなんかに見せるもんですか！",
    404: "そんなページ知らないっ！",
    405: "そのMethodはNot Allowedだよ",
    406: "すまんがその条件ではお渡しできない。",
    407: "うちのプロキシ使うんだったらまずログインしな。",
    408: "もう用がないならさっさと帰りなさい。",
    409: "ちょっと待ったそんな話聞いてないぞ",
    410: "もう無いで。",
    411: "サイズを教えろ。話はそれからだ。",
    412: "なにその条件美味しいの",
    413: "そ、そそ、そんなの入りきらないよっ！",
    414: "もちつけ",
    415: "そんな形式知らない！",
    416: "そんな大きく...ない...んだ...",
    417: "期待させて悪かったわね！",
    418: "ティーポット「私はコーヒーを注ぐためのものではありません！やだっ！」"
}

def error_page(templates: Jinja2Templates, request: Request, status_code: int, message: str | None = None) -> Response:
    status_code_name = HTTPStatus(status_code).phrase
    return templates.TemplateResponse(status_code=status_code, request=request, name="error.html", context={"status_code": status_code, "status_code_name": status_code_name, "message": message or default_messages.get(status_code, "あんのーん")})
