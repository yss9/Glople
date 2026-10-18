"""도구 결과 봉투(design.md 8.1).

- content(LLM용): {"tool", "status", "items", "notes"} JSON, ensure_ascii=False, 1,000자 상한.
  items는 어댑터가 허용 필드(whitelist)로 만든 값만 받는다. 여기서는 모든 문자열을 한 번 더 정제한다.
- card(화면용): state.Card 모양. ToolMessage.artifact에 실려 모델 요청에는 들어가지 않는다(원문은 여기에만).
  turn_id는 도구가 모르므로 collect가 bind_card / card_from_tool_message로 채운다.
- 오류: ToolNode(handle_tool_errors=tool_error_to_json)가 예외를 {"tool","status":"error","error","message_ko"}로 바꾼다.
  예외 문자열·인자는 content에 넣지 않는다(토큰·주입 문자열이 섞일 수 있음).
"""
import copy
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal, get_args

import httpx
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolInvocationError
from pydantic import ValidationError

from glople_agent.answer.templates import error_message, status_message
from glople_agent.sanitize import clean_text
from glople_agent.state import Card, CardKind, ResultStatus
from glople_agent.tools.spring import SpringError

MAX_CONTENT_CHARS = 1000   # 도구당 content 상한(한글 약 1,000자 ≈ 800토큰)
MAX_LLM_ITEMS = 5          # 어댑터가 더 넘겨도 LLM에는 5개까지
MAX_LLM_STR = 120          # LLM용 문자열 하나의 상한(필드별 상한은 어댑터가 더 짧게)
MAX_NOTE_STR = 200

ErrorCode = Literal["upstream_unavailable", "timeout", "bad_request"]
STATUSES: tuple[str, ...] = get_args(ResultStatus)

CARD_KINDS: dict[str, CardKind] = {
    "recommend_by_keywords": "destination_list",
    "search_destinations": "destination_list",
    "recommend_similar_traveler_routes": "traveler_routes",
    "get_destination_detail": "destination_detail",
    "search_reviews": "review_list",
    "search_gloplers": "glopler_list",
    "get_weather": "weather",
    "create_travel_plan": "plan_confirm",
}


class ToolBackendError(Exception):
    """도구 실행 실패. registry가 백엔드 예외를 이것으로 감싸 도구 이름을 붙인다(tool_error_to_json이 e만 받으므로)."""

    def __init__(self, tool: str, code: ErrorCode = "upstream_unavailable") -> None:
        super().__init__(f"{tool}: {code}")   # 원래 예외 문자열은 __cause__에만 남긴다
        self.tool = tool
        self.code: ErrorCode = code


def card_id_for(tool: str, call_id: str) -> str:
    return f"{tool}:{call_id}"


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _clean(value: Any, max_len: int) -> Any:
    """LLM으로 가는 값의 모든 문자열을 정제한다(HTML·특수 토큰 제거, 길이 제한). JSON 타입만 남긴다."""
    if isinstance(value, str):
        return clean_text(value, max_len)
    if isinstance(value, bool) or value is None or isinstance(value, int | float):
        return value
    if isinstance(value, Mapping):
        return {str(k): _clean(v, max_len) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v, max_len) for v in value]
    raise TypeError(f"LLM용 결과에는 JSON 값만 넣습니다: {type(value).__name__}")


def _fit(tool: str, status: str, items: list, notes: list[str], max_chars: int) -> str:
    """상한을 넘으면 뒤 항목부터 빼고(카드에는 남음), 그래도 넘으면 notes를 줄인다. 항상 올바른 JSON."""
    body = {"tool": tool, "status": status, "items": items, "notes": notes}
    text = _dumps(body)
    if len(text) <= max_chars:
        return text
    kept = list(items)
    while kept:
        kept.pop()
        dropped = len(items) - len(kept)
        trial = {"tool": tool, "status": status, "items": kept,
                 "notes": [*notes, f"길이 제한으로 {dropped}개는 화면 카드에만 있어요."]}
        text = _dumps(trial)
        if len(text) <= max_chars:
            return text
    short_notes = [n[:60] for n in notes][:3]
    while True:
        text = _dumps({"tool": tool, "status": status, "items": [], "notes": short_notes})
        if len(text) <= max_chars or not short_notes:
            break
        short_notes.pop()
    if len(text) > max_chars:  # 도구 이름까지 비정상적으로 긴 경우
        raise ValueError(f"결과 봉투가 {max_chars}자 안에 들어가지 않습니다")
    return text


def make_result(tool: str, status: ResultStatus, items: Sequence[Mapping[str, Any]] = (),
                notes: Sequence[str] = (), *, card_items: Sequence[Mapping[str, Any]] | None = None,
                card_data: Mapping[str, Any] | None = None, call_id: str = "",
                max_chars: int = MAX_CONTENT_CHARS) -> tuple[str, Card]:
    """(LLM용 content JSON, 카드)를 만든다.

    items: LLM에 보낼 항목(허용 필드만). card_items: 카드에 보일 항목(원문·출처 포함, 없으면 items).
    card_data: 카드 data에 더할 값(args 등). call_id: tool_call_id(registry가 InjectedToolCallId로 받음).
    """
    if status not in STATUSES:
        raise ValueError(f"모르는 status: {status!r}")
    llm_items = [_clean(dict(i), MAX_LLM_STR) for i in list(items)[:MAX_LLM_ITEMS]]
    llm_notes = [clean_text(n, MAX_NOTE_STR) for n in notes if n and n.strip()]
    content = _fit(tool, status, llm_items, llm_notes, max_chars)
    data: dict[str, Any] = {
        "items": [dict(i) for i in (items if card_items is None else card_items)],
        "notes": llm_notes,
    }
    data.update(card_data or {})
    card: Card = {
        "card_id": card_id_for(tool, call_id) if call_id else "",
        "turn_id": "",
        "call_id": call_id,
        "tool": tool,
        "kind": CARD_KINDS.get(tool, "notice"),
        "status": status,
        "data": data,
    }
    return content, card


def classify_error(e: BaseException) -> ErrorCode:
    if isinstance(e, ToolBackendError | SpringError):
        return e.code
    if isinstance(e, PermissionError):   # 허용 목록 밖 경로(우리 코드의 실수) → 요청 거절로
        return "bad_request"
    if isinstance(e, ToolInvocationError | ValidationError):
        return "bad_request"
    if isinstance(e, TimeoutError | httpx.TimeoutException):
        return "timeout"
    if isinstance(e, httpx.HTTPStatusError):
        return "bad_request" if 400 <= e.response.status_code < 500 else "upstream_unavailable"
    return "upstream_unavailable"


def _error_tool(e: BaseException) -> str | None:
    if isinstance(e, ToolBackendError):
        return e.tool
    if isinstance(e, ToolInvocationError):
        return e.tool_name
    return None


def tool_error_to_json(e: Exception) -> str:
    """ToolNode의 handle_tool_errors. 매개변수 타입이 Exception이라 모든 예외를 ToolMessage(error)로 바꾼다."""
    tool = _error_tool(e)
    code = classify_error(e)
    return _dumps({"tool": tool, "status": "error", "error": code, "message_ko": error_message(tool, code)})


def bind_card(card: Mapping[str, Any], *, turn_id: str, call_id: str | None = None) -> Card:
    """artifact 카드에 이번 턴 id를 붙인 새 카드(원본은 그대로). call_id를 주면 card_id도 다시 만든다."""
    call = call_id or card.get("call_id") or ""
    tool = card.get("tool") or ""
    return {
        "card_id": card_id_for(tool, call) if call else card.get("card_id", ""),
        "turn_id": turn_id,
        "call_id": call,
        "tool": tool,
        "kind": card.get("kind") or CARD_KINDS.get(tool, "notice"),
        "status": card.get("status") or "error",
        "data": copy.deepcopy(dict(card.get("data") or {})),
    }


def parse_content(content: Any) -> dict | None:
    """ToolMessage.content가 이 봉투(또는 오류 JSON)면 dict, 아니면 None."""
    if not isinstance(content, str):
        return None
    try:
        obj = json.loads(content)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) and obj.get("status") in STATUSES else None


def card_from_tool_message(msg: ToolMessage, *, turn_id: str, args: Mapping[str, Any] | None = None) -> Card:
    """collect용: ToolMessage 하나 → 카드. artifact가 있으면 그것을, 없으면(오류) content로 오류 카드를 만든다."""
    artifact = msg.artifact
    if isinstance(artifact, Mapping) and artifact.get("kind"):
        return bind_card(artifact, turn_id=turn_id, call_id=msg.tool_call_id)
    parsed = parse_content(msg.content) or {}
    tool = msg.name or parsed.get("tool") or ""
    status = parsed.get("status") or "error"
    if status == "error":
        code = parsed.get("error") or "upstream_unavailable"
        message = parsed.get("message_ko") or error_message(tool, code)
        data = {"items": [], "notes": [message], "error": code, "args": dict(args or {})}
    else:   # artifact 없이 봉투만 온 경우(직접 만든 ToolMessage 등)
        notes = [n for n in parsed.get("notes") or [] if isinstance(n, str)]
        data = {"items": list(parsed.get("items") or []),
                "notes": notes or [status_message(tool, status, args)], "args": dict(args or {})}
    return {
        "card_id": card_id_for(tool, msg.tool_call_id),
        "turn_id": turn_id,
        "call_id": msg.tool_call_id,
        "tool": tool,
        "kind": CARD_KINDS.get(tool, "notice"),
        "status": status,
        "data": data,
    }
