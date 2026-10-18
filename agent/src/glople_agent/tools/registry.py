"""읽기 도구 7개 → StructuredTool(design.md 8.1). create_travel_plan은 ToolNode에 넣지 않는다(9절).

- 이름·설명은 contract의 glople_tools.json에서, args_schema는 args.py에서 가져온다.
- response_format="content_and_artifact": 함수가 (LLM용 JSON, 카드)를 돌려주고 카드는 ToolMessage.artifact로 간다.
  ChatOpenAI는 ToolMessage의 role·content·tool_call_id만 보내므로 Type3 원문(카드)은 모델 요청에 없다.
- tool_call_id는 InjectedToolCallId로 받는다(모델 스키마에는 안 보임). 실행 문맥은 langgraph.runtime.get_runtime()에서
  now·seed만 꺼낸다(ToolContext, 토큰 없음). 도구는 ToolCall 모양으로 불러야 한다:
  tool.invoke({"type": "tool_call", "id": "call_1", "name": 이름, "args": {...}}).
- 백엔드 예외는 ToolBackendError(이름, 코드)로 감싸 다시 던진다. ToolNode(handle_tool_errors=tool_error_to_json)가
  ToolMessage(status="error")로 바꾸고, 병렬로 부른 다른 도구 결과는 그대로 남는다.
"""
import inspect
from typing import Annotated, Any

from langchain_core.tools import InjectedToolCallId, StructuredTool
from langgraph.runtime import get_runtime

from glople_agent import contract
from glople_agent.answer.templates import status_message
from glople_agent.tools.args import ARGS_MODELS
from glople_agent.tools.backend import BackendResult, ReadBackend, ToolContext
from glople_agent.tools.envelope import ToolBackendError, classify_error, make_result


def _description(name: str) -> str:
    for t in contract.TOOLS:
        if t["function"]["name"] == name:
            return t["function"]["description"]
    raise KeyError(name)


def _accepts_ctx(method: Any) -> bool:
    try:
        return len(inspect.signature(method).parameters) >= 2
    except (TypeError, ValueError):
        return False


def run_tool(name: str, backend: ReadBackend, args: dict[str, Any], call_id: str,
             ctx: ToolContext | None = None) -> tuple[str, dict]:
    """도구 하나 실행: 인자 모델 → 백엔드 → 봉투. ok가 아니면 이유 문장을 notes 맨 앞에 둔다(LLM도 이유를 봄).
    ctx(now·seed)는 그래프 안에서는 runtime.context(get_runtime)에서 오고, 직접 부르면 None(백엔드 시계)."""
    model = ARGS_MODELS[name].model_validate({k: v for k, v in args.items() if v is not None})
    method = getattr(backend, name)
    try:
        result: BackendResult = method(model, ctx) if _accepts_ctx(method) else method(model)
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


def current_tool_context() -> ToolContext | None:
    """지금 도는 그래프의 runtime.context(AgentContext)에서 now·seed만 꺼낸다. 그래프 밖이면 None.

    ToolRuntime 인자 주입(`runtime: ToolRuntime`)은 쓰지 않는다: ToolNode가 runtime을 args에 넣은 채
    args_schema(extra="forbid")로 검증해 ToolInvocationError가 난다(api-notes, test_pins_langgraph).
    토큰·사용자 id는 도구로 옮기지 않는다(공개 API만 부름, 최소 권한).
    """
    try:
        runtime = get_runtime()
    except (RuntimeError, KeyError):   # 실행 config가 없거나(그래프·Runnable 밖) configurable이 없음
        return None
    return ToolContext.from_agent_context(getattr(runtime, "context", None)) if runtime is not None else None


def _make_tool(name: str, backend: ReadBackend) -> StructuredTool:
    def run(tool_call_id: Annotated[str, InjectedToolCallId], **kwargs: Any) -> tuple[str, dict]:
        return run_tool(name, backend, kwargs, tool_call_id, current_tool_context())

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
