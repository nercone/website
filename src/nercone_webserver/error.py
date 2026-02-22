from http import HTTPStatus
from fastapi import Request, Response
from fastapi.templating import Jinja2Templates

def error_page(templates: Jinja2Templates, request: Request, status_code: int, message: str | None = None) -> Response:
    status_code_name = HTTPStatus(status_code).phrase
    return templates.TemplateResponse(status_code=status_code, request=request, name="error.html", context={"status_code": status_code, "status_code_name": status_code_name, "message": message})
