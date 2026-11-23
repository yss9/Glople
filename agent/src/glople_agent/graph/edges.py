"""조건부 엣지(design.md 5.4). 엣지 함수는 state만 읽고 다음 노드 이름을 돌려준다(state를 바꾸지 않음).

remaining_steps 기준(api-notes '동작 고정'): 엣지는 방금 돈 노드와 같은 remaining_steps를 본다.
노드가 k개 더 돌려면 remaining_steps ≥ k+1이어야 하고(마지막 노드도 1이 남아야 END), 모자라면
GraphRecursionError가 난다. 그래서 route 뒤로 가장 긴 경로가 들어갈 때만 그 길로 보낸다.

(M7 loop) 도구 결과 뒤 루프는 기회가 될 때만 돈다: collect·route_after_tools가 단계가 남을 때만 한 라운드 더 가고, 모자라면
answer_template으로 끝낸다. 그래서 route가 보는 도구 경로 길이(steps_after_route)는 answer 모드와 같다.
"""
from langchain_core.messages import AIMessage

from glople_agent.config import AfterToolsMode, AnswerMode
from glople_agent.contract import PLAN_TOOL_NAME
from glople_agent.graph.answer_input import llm_answerable
from glople_agent.state import ParentState

ROUTE_RETRY_ATTEMPTS = 2   # RetryPolicy(max_attempts): 연결 오류일 때 첫 실행을 포함한 총 횟수
MAX_ROUTER_ATTEMPTS = 2    # quirk 자기 루프(route → route): 첫 시도 + 재시도 1회
RETRY_KINDS = ("dropped", "truncated", "unparsed")

# route 뒤 가장 긴 경로의 노드 수: validate → tools → collect → answer_template → finish (llm 모드는 answer_llm 하나 더)
_STEPS_AFTER_ROUTE: dict[str, int] = {"template": 5, "llm": 6}

# M7 loop: 도구 결과 뒤 라운드 상한과, 한 번 더 가려면 남아야 하는 remaining_steps(노드 k개 + 1)
MAX_TOOL_ROUNDS = 2
STEPS_ROUTE_AFTER_TOOLS = 4   # route_after_tools → answer_template → finish
STEPS_LOOP_CALL = 6           # validate_after_tools → tools → collect → answer_template → finish


def steps_after_route(answer_mode: AnswerMode = "template") -> int:
    return _STEPS_AFTER_ROUTE[answer_mode]


def min_steps_for_tools(answer_mode: AnswerMode = "template") -> int:
    """route가 본 remaining_steps가 이만큼은 있어야 도구 경로가 END까지 간다."""
    return steps_after_route(answer_mode) + 1


def min_steps_to_retry(answer_mode: AnswerMode = "template") -> int:
    """다시 도는 route 하나 + 그 뒤 가장 긴 경로 + 1 (M0 템플릿 7, M2 llm 8)."""
    return steps_after_route(answer_mode) + 2


def make_after_route(answer_mode: AnswerMode = "template"):
    need_tools = min_steps_for_tools(answer_mode)
    need_retry = min_steps_to_retry(answer_mode)

    def after_route(state: ParentState) -> str:
        kind = state.get("router_kind")
        steps = state["remaining_steps"]
        if kind == "tool":
            return "validate" if steps >= need_tools else "fallback"
        if kind == "text":
            return "direct_reply"
        if (kind in RETRY_KINDS and state.get("router_attempts", 0) < MAX_ROUTER_ATTEMPTS
                and steps >= need_retry):
            return "route"   # 자기 루프: 빈 호출·잘림·파싱 실패는 한 번만 다시
        return "fallback"    # 재시도 초과·단계 부족·모델 서버 오류

    return after_route


def after_validate(state: ParentState) -> str:
    verdicts = state.get("verdicts") or []
    passed = [v for v in verdicts if v["ok"]]
    if any(v["name"] == PLAN_TOOL_NAME for v in passed):
        return "plan_prepare"   # validate가 일정 호출을 혼자 남겨 둠
    if passed:
        return "tools"
    if any(v.get("clarify") for v in verdicts):
        return "clarify"
    return "fallback"           # 통과 0개, 되물을 것도 없음(모르는 도구·모르는 키 …)


def make_after_collect(answer_mode: AnswerMode = "template", after_tools: AfterToolsMode = "answer"):
    """after_tools="loop"(M7)이면 답변 노드 대신 route_after_tools로 보낸다. 동명 장소 후보가 섞였거나 단계가 모자라면 템플릿."""
    def after_collect(state: ParentState) -> str:
        results = state.get("results") or []
        # M6: 이번 결과가 모두 되물을 결과(동명 장소 → 후보 카드, collect가 results에 clarify=True)면 clarify 노드로.
        # 다른 결과와 섞였으면 답변 경로에서 그 되묻기 문장을 함께 말한다(answer_input이 짝째로 빼고 문장을 앞에)
        if results and all(r.get("clarify") for r in results):
            return "clarify"
        if after_tools == "loop":
            # 후보 되묻기가 섞이면 템플릿(카드 문장이 '어느 지역의 ○○인가요?'를 말함). 학습 기록에 없는 섞임이라 모델에 맡기지 않음
            if any(r.get("clarify") for r in results) or state["remaining_steps"] < STEPS_ROUTE_AFTER_TOOLS:
                return "answer_template"
            return "route_after_tools"
        # llm 모드여도 LLM이 답할 결과(ok, 날씨 제외)가 하나도 없으면 템플릿으로(빈 결과·미지원·오류는 이유를 그대로)
        if answer_mode == "llm" and any(llm_answerable(r.get("tool"), r.get("status")) and not r.get("clarify")
                                        for r in results):
            return "answer_llm"
        return "answer_template"   # 템플릿 모드(M0 기본)는 answer_llm에 닿지 않음

    return after_collect


def _pending_call(state: ParentState) -> bool:
    """마지막 메시지가 아직 실행하지 않은 호출(route_after_tools가 넣고 validate_after_tools가 남긴 AIMessage)인지."""
    messages = state.get("messages") or []
    return bool(messages) and isinstance(messages[-1], AIMessage) and bool(messages[-1].tool_calls)


def after_route_after_tools(state: ParentState) -> str:
    """M7: 가드를 통과한 글 → finish, 받은 호출 → validate_after_tools, 그 밖(가드 실패·상한·모델 오류) → answer_template."""
    if state.get("reply_kind") == "llm" and state.get("final_text"):
        return "finish"
    return "validate_after_tools" if _pending_call(state) else "answer_template"


def after_validate_after_tools(state: ParentState) -> str:
    """M7: 통과한 호출이 남았으면 tools(같은 ToolNode), 모두 막혔으면 앞 결과로 템플릿 답."""
    return "tools" if _pending_call(state) and state["remaining_steps"] >= STEPS_LOOP_CALL - 1 else "answer_template"


def after_answer(state: ParentState) -> str:
    # 가드를 통과한 LLM 답(reply_kind="llm")만 그대로 끝냄. 가드 실패·모델 오류·모델 없음은 같은 턴의 템플릿 답으로
    return "finish" if state.get("reply_kind") == "llm" and state.get("final_text") else "answer_template"


def fallback_reason(state: ParentState) -> str:
    """fallback 노드가 고를 안내문 키(templates.FALLBACK_MESSAGES)."""
    kind = state.get("router_kind")
    trace = state.get("trace") or {}
    if kind == "error":
        last = (trace.get("route") or [{}])[-1]
        return "timeout" if (last.get("detail") or {}).get("error") == "timeout" else "model_error"
    if kind in RETRY_KINDS:
        return "unparsed" if state.get("router_attempts", 0) >= MAX_ROUTER_ATTEMPTS else "steps"
    if kind == "tool":
        return "no_valid_call" if "validate" in trace else "steps"
    return "unparsed"
