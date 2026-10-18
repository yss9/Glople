"""오류 응답: Spring과 같은 JSON `{status, error, message, path}`(docs/authorization.md '오류 응답') + 기계용 `code`.

- 401 = 토큰 없음·만료·무효, 403 = 남의 것·없는 것(스레드·일정 모두 존재 여부를 숨김, C01과 같음).
- 메시지에는 요청 값(본문·토큰·발화)을 넣지 않는다. 422의 errors도 칸 이름과 종류만(입력 값 없음).
"""
from http import HTTPStatus
from typing import Any

from fastapi.responses import JSONResponse

LOGIN_REQUIRED = "로그인이 필요합니다."
FORBIDDEN = "권한이 없습니다."


class ApiError(Exception):
    """핸들러가 그대로 JSON으로 바꾸는 오류. code는 화면이 나눠 쓰는 이름(THREAD_BUSY 등)."""

    def __init__(self, status: int, code: str, message: str, *, errors: list[dict] | None = None,
                 retryable: bool = False) -> None:
        super().__init__(f"{status} {code}")   # 메시지에 요청 값을 넣지 않는다
        self.status = status
        self.code = code
        self.message = message
        self.errors = errors
        self.retryable = retryable


def unauthorized(message: str = LOGIN_REQUIRED) -> ApiError:
    return ApiError(401, "UNAUTHORIZED", message)


def forbidden() -> ApiError:
    return ApiError(403, "FORBIDDEN", FORBIDDEN)


def error_body(status: int, code: str, message: str, path: str, *, errors: list[dict] | None = None,
               retryable: bool | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"status": status, "error": HTTPStatus(status).name, "message": message, "path": path,
                            "code": code}
    if errors is not None:
        body["errors"] = errors
    if retryable is not None:
        body["retryable"] = retryable
    return body


def error_response(status: int, code: str, message: str, path: str, *, errors: list[dict] | None = None,
                   retryable: bool | None = None, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(error_body(status, code, message, path, errors=errors, retryable=retryable),
                        status_code=status, headers=headers)


def validation_errors(raw: list[dict]) -> list[dict]:
    """pydantic 오류 → [{field, code, message}]. 입력 값(input)·ctx는 싣지 않는다(토큰·발화가 섞일 수 있음)."""
    out = []
    for e in raw:
        loc = [str(p) for p in e.get("loc", ()) if p not in ("body", "path", "query")]
        code = str(e.get("type") or "invalid")
        out.append({"field": ".".join(loc) or "body", "code": code, "message": _message_for(code)})
    return out


_MESSAGES = {
    "extra_forbidden": "받지 않는 칸입니다.",
    "missing": "필요한 값이 없습니다.",
    "string_too_long": "글이 너무 깁니다.",
    "string_too_short": "글이 비어 있습니다.",
    "too_long": "항목이 너무 많습니다.",
    "literal_error": "허용되지 않는 값입니다.",
    "json_invalid": "JSON 본문을 읽지 못했습니다.",
}


def _message_for(code: str) -> str:
    return _MESSAGES.get(code, "값이 올바르지 않습니다.")


__all__ = ["ApiError", "FORBIDDEN", "LOGIN_REQUIRED", "error_body", "error_response", "forbidden", "unauthorized",
           "validation_errors"]
