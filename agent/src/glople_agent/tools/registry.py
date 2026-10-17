"""읽기 도구 7개 → StructuredTool(design.md 8.1). create_travel_plan은 ToolNode에 넣지 않는다(9절).

- 이름·설명은 contract의 glople_tools.json에서, args_schema는 args.py에서 가져온다.
- response_format="content_and_artifact": 함수가 (LLM용 JSON, 카드)를 돌려주고 카드는 ToolMessage.artifact로 간다.
  ChatOpenAI는 ToolMessage의 role·content·tool_call_id만 보내므로 Type3 원문(카드)은 모델 요청에 없다.
- tool_call_id는 InjectedToolCallId로 받는다(모델 스키마에는 안 보임). 그래서 도구는 ToolCall 모양으로 불러야 한다:
  tool.invoke({"type": "tool_call", "id": "call_1", "name": 이름, "args": {...}}).
- 백엔드 예외는 ToolBackendError(이름, 코드)로 감싸 다시 던진다. ToolNode(handle_tool_errors=tool_error_to_json)가
  ToolMessage(status="error")로 바꾸고, 병렬로 부른 다른 도구 결과는 그대로 남는다.
"""
from typing import Annotated, Any

from langchain_core.tools import InjectedToolCallId, StructuredTool

from glople_agent import contract
from glople_agent.answer.templates import status_message
from glople_agent.tools.args import ARGS_MODELS
from glople_agent.tools.envelope import ToolBackendError, classify_error, make_result
from glople_agent.tools.fake_backend import BackendResult, ReadBackend


def _description(name: str) -> str:
    for t in contract.TOOLS:
        if t["function"]["name"] == name:
            return t["function"]["description"]
    raise KeyError(name)


def run_tool(name: str, backend: ReadBackend, args: dict[str, Any], call_id: str) -> tuple[str, dict]:
    """도구 하나 실행: 인자 모델 → 백엔드 → 봉투. ok가 아니면 이유 문장을 notes 맨 앞에 둔다(LLM도 이유를 봄)."""
    model = ARGS_MODELS[name].model_validate({k: v for k, v in args.items() if v is not None})
    try:
        result: BackendResult = getattr(backend, name)(model)
    except ToolBackendError:
        raise
    except Exception as e:  # noqa: BLE001 - 어떤 백엔드 예외든 도구 이름을 붙여 ToolNode로 넘김
        raise ToolBackendError(name, classify_error(e)) from e
    used_args = model.model_dump(exclude_none=True)
    notes = list(result.notes)
    message = None
    if result.status != "ok":
        message = status_message(name, result.status, used_args)
        notes.insert(0, message)
    return make_result(name, result.status, result.items, notes, card_items=result.card_items,
                       card_data={"args": used_args, "message": message, **result.extra}, call_id=call_id)


def _make_tool(name: str, backend: ReadBackend) -> StructuredTool:
    def run(tool_call_id: Annotated[str, InjectedToolCallId], **kwargs: Any) -> tuple[str, dict]:
        return run_tool(name, backend, kwargs, tool_call_id)

    run.__name__ = name
    return StructuredTool.from_function(
        func=run,
        name=name,
        description=_description(name),
        args_schema=ARGS_MODELS[name],
        response_format="content_and_artifact",
    )


def build_read_tools(backend: ReadBackend) -> list[StructuredTool]:
    """읽기 도구 7개(contract.READ_TOOL_NAMES 순서). ToolNode(build_read_tools(...), handle_tool_errors=tool_error_to_json)."""
    missing = [n for n in contract.READ_TOOL_NAMES if not callable(getattr(backend, n, None))]
    if missing:
        raise TypeError(f"백엔드에 도구 메서드가 없습니다: {missing}")
    return [_make_tool(name, backend) for name in contract.READ_TOOL_NAMES]
